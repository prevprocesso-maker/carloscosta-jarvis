"""JARVIS COWORK — o programador do JARVIS (05/10/2026).

O dono escolhe uma pasta (o "projeto aberto") e pede por voz ou por escrito:
"faz um jogo da cobrinha", "a cor do botao esta errada", "deu erro ao abrir".

Um pedido, do comeco ao fim:
  1. Se o projeto ja roda, testa como ele esta AGORA (erros + foto da tela), para
     o Codex ver o que o dono esta vendo.
  2. Compacta a pasta (sem node_modules, .git...) e manda para o Codex da VPS
     (jarvis-cowork.js), que trabalha numa copia e devolve SO o que mudou.
  3. Aplica na pasta, guardando antes um ponto de "desfaz".
  4. Roda o projeto escondido (pagina no navegador sem janela, ou o programa),
     le os erros, tira a foto da tela.
  5. Se achou erro (ou foi um trabalho grande), manda uma rodada de verificacao
     com o relatorio e a foto; o Codex corrige ou responde OK. Ate 3 rodadas.
  6. Abre o resultado para o dono ver e devolve a frase para ser falada.

Nada disto toca no resto do JARVIS: roda numa thread propria, fala com a VPS
por mensagens proprias (cw_*) e so escreve dentro da pasta do projeto.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
_CONFIG = BASE_DIR / "config" / "cowork.json"

PASTA_INTERNA = ".jarvis_cowork"          # desfaz e historico, dentro do projeto
ARQ_COMO_RODAR = ".jarvis-cowork.json"    # o Codex escreve como rodar o projeto

# Nunca sobem (pesadas, geradas por ferramenta ou do proprio Cowork).
_IGNORAR_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv", "env",
    PASTA_INTERNA, ".pytest_cache", ".mypy_cache", ".idea", ".vs", ".next", ".nuxt",
    ".cache", ".parcel-cache", ".turbo", "dist", "build", "out", "bin", "obj",
    "target", ".gradle", ".dart_tool", "Pods", ".expo",
}
_IGNORAR_ARQ = {"thumbs.db", "desktop.ini", ".ds_store"}
# Codigo e texto sobem sempre; o resto (imagem, som...) so se for pequeno.
_TEXTO = {
    ".html", ".htm", ".css", ".scss", ".sass", ".less", ".js", ".mjs", ".cjs", ".jsx",
    ".ts", ".tsx", ".vue", ".svelte", ".json", ".jsonc", ".md", ".txt", ".py", ".pyw",
    ".java", ".kt", ".kts", ".c", ".h", ".cpp", ".hpp", ".cc", ".cs", ".go", ".rs",
    ".rb", ".php", ".swift", ".dart", ".lua", ".sql", ".sh", ".bat", ".cmd", ".ps1",
    ".yml", ".yaml", ".toml", ".ini", ".cfg", ".conf", ".xml", ".svg", ".csv", ".env.example",
    ".gitignore", ".editorconfig", ".gd", ".tscn", ".godot", ".shader", ".glsl",
}
_MAX_ZIP = 38 * 1024 * 1024
_MAX_ARQUIVOS = 6000
_MAX_BINARIO = 1024 * 1024
_RODADAS_EXTRAS = 3
_TIMEOUT_RODADA = 30 * 60.0          # a VPS corta em 25 min; folga para fila/envio
_MAX_PONTOS = 30


class CoworkCancelado(Exception):
    pass


# O modelo as vezes conclui que "nao tem acesso" sem nem tentar (pasta vazia).
_ACHOU_QUE_NAO_PODIA = re.compile(
    r"somente leitura|read[- ]?only|n[aã]o consegui acessar|sem (?:acesso|permiss)|"
    r"permiss[aã]o de escrita|acesso de escrita|n[aã]o (?:tenho|tive) (?:acesso|permiss)", re.I)


def _agora() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def _sem_acento(texto: str) -> str:
    import unicodedata
    t = unicodedata.normalize("NFD", str(texto or ""))
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def _dentro(pasta: Path, alvo: Path) -> bool:
    try:
        alvo.resolve().relative_to(pasta.resolve())
        return True
    except Exception:
        return False


def _nome_do_zip(info: zipfile.ZipInfo) -> str:
    """Nome do arquivo DENTRO do zip, com acento certo.

    O tar da VPS gravava nomes na codificacao antiga do Windows (cp850) sem a
    marca de UTF-8: "acao.txt" com cedilha chegava embaralhado. Sem a marca,
    o Python le como cp437; aqui volta aos bytes e tenta UTF-8, depois cp850."""
    if info.flag_bits & 0x800:
        return info.filename
    try:
        bruto = info.filename.encode("cp437")
    except UnicodeEncodeError:
        return info.filename
    for cod in ("utf-8", "cp850"):
        try:
            return bruto.decode(cod)
        except UnicodeDecodeError:
            continue
    return info.filename


def _nome_seguro(rel: str) -> str | None:
    """Caminho relativo vindo da VPS: so aceita o que fica DENTRO do projeto."""
    r = str(rel or "").replace("\\", "/").strip()
    while r.startswith("./"):
        r = r[2:]
    if not r or r.endswith("/") or r.startswith("/") or re.match(r"^[A-Za-z]:", r):
        return None
    partes = [p for p in r.split("/") if p not in ("", ".")]
    if not partes or any(p == ".." for p in partes):
        return None
    if partes[0] in (PASTA_INTERNA, "_jarvis_cowork") or partes[0] in {".git"}:
        return None
    return "/".join(partes)


class Cowork:
    """Uma sessao de Cowork por JARVIS (um projeto aberto de cada vez)."""

    def __init__(self):
        self.pasta: Path | None = None
        self._ocupado = False
        self._trava = threading.Lock()
        self._cancelar = threading.Event()
        self._ouvintes: list = []
        self.etapa = ""
        self.linhas: list[str] = []
        self._servidor = None
        self._servidor_pasta: Path | None = None
        self._servidor_porta = 0
        self._processo = None            # o programa aberto para o dono ver
        self._ultimo_teste: dict | None = None
        self._ultimo_teste_em = 0.0
        self._janela_app = None          # janela propria do projeto web (Edge/Chrome --app)
        self._carregar_config()

    # ── estado para a tela ──────────────────────────────────────────────────
    def ouvir(self, funcao) -> None:
        if funcao not in self._ouvintes:
            self._ouvintes.append(funcao)

    def estado(self) -> dict:
        return {
            "pasta": str(self.pasta) if self.pasta else "",
            "nome": self.pasta.name if self.pasta else "",
            "ocupado": self._ocupado,
            "etapa": self.etapa,
            "linhas": list(self.linhas[-5:]),
            "pode_desfazer": bool(self._pontos()) if self.pasta else False,
        }

    def _avisar(self, etapa: str | None = None, linha: str | None = None) -> None:
        if etapa is not None:
            self.etapa = etapa
            if etapa:
                self.linhas.append(etapa)
        if linha:
            linha = str(linha).strip()[:150]
            if linha and (not self.linhas or self.linhas[-1] != linha):
                self.linhas.append(linha)
        self.linhas = self.linhas[-30:]
        est = self.estado()
        for f in list(self._ouvintes):
            try:
                f(est)
            except Exception:
                pass

    def _checar_cancelado(self) -> None:
        if self._cancelar.is_set():
            raise CoworkCancelado()

    # ── configuracao (lembra o ultimo projeto) ──────────────────────────────
    def _carregar_config(self) -> None:
        try:
            dados = json.loads(_CONFIG.read_text(encoding="utf-8"))
            p = Path(str(dados.get("pasta") or ""))
            if str(p) and p.is_dir() and not self._proibida(p):
                self.pasta = p
        except Exception:
            pass

    def _salvar_config(self) -> None:
        try:
            _CONFIG.parent.mkdir(parents=True, exist_ok=True)
            recentes = []
            try:
                recentes = json.loads(_CONFIG.read_text(encoding="utf-8")).get("recentes") or []
            except Exception:
                pass
            if self.pasta:
                recentes = [str(self.pasta)] + [r for r in recentes if r != str(self.pasta)]
            _CONFIG.write_text(json.dumps({"pasta": str(self.pasta or ""), "recentes": recentes[:10]},
                                          ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception:
            pass

    # ── escolher / criar o projeto ──────────────────────────────────────────
    @staticmethod
    def _proibida(p: Path) -> str:
        """Pastas que NAO podem virar projeto (o Codex mexeria em tudo)."""
        try:
            r = p.resolve()
        except Exception:
            return "pasta invalida"
        casa = Path.home().resolve()
        if r.parent == r:
            return "a raiz do disco"
        largas = {casa, casa / "Desktop", casa / "Documents", casa / "Downloads",
                  casa / "Pictures", casa / "Music", casa / "Videos", casa / "OneDrive",
                  casa / "OneDrive" / "Desktop", casa / "OneDrive" / "Documentos",
                  casa / "OneDrive" / "Documents", casa / "Área de Trabalho"}
        if r in {x.resolve() if x.exists() else x for x in largas}:
            return "uma pasta pessoal inteira"
        baixo = str(r).lower().replace("\\", "/")
        for proibido in ("c:/windows", "c:/program files", "c:/programdata",
                         "/appdata/local/microsoft", "/appdata/roaming/microsoft"):
            if baixo.startswith(proibido) or proibido in baixo and proibido.startswith("/"):
                return "uma pasta do sistema"
        try:
            r.relative_to(BASE_DIR.resolve())
            return "a pasta do próprio JARVIS"
        except Exception:
            pass
        return ""

    def abrir_pasta(self, caminho, criar: bool = False) -> str:
        if self._ocupado:
            return "Espera eu terminar o trabalho atual do Cowork antes de trocar de projeto."
        p = Path(str(caminho or "").strip().strip('"').strip("'")).expanduser()
        if criar and not p.exists():
            try:
                p.mkdir(parents=True, exist_ok=True)
            except Exception as exc:
                return f"Não consegui criar a pasta: {str(exc)[:80]}"
        if not p.is_dir():
            return "Não achei essa pasta."
        motivo = self._proibida(p)
        if motivo:
            return (f"Essa pasta é {motivo}. Para o Cowork, escolha ou crie uma pasta "
                    "só do projeto (por exemplo, dentro de Documentos).")
        self._parar_processo()
        self.pasta = p.resolve()
        self._ultimo_teste = None
        self.linhas = []
        self._salvar_config()
        self._avisar(etapa="")
        return f"Cowork aberto na pasta {self.pasta.name}. Pode pedir o que quer que eu faça nela."

    def fechar_janela(self) -> str:
        """Fecha a janela/programa do projeto que o Cowork abriu."""
        self._parar_processo()
        return "Fechei a janela do projeto."

    def fechar(self) -> str:
        if self._ocupado:
            return "Estou no meio de um trabalho; diga parar o cowork antes."
        self._parar_processo()
        self._parar_servidor()
        self.pasta = None
        self._salvar_config()
        self._avisar(etapa="")
        return "Fechei o projeto do Cowork."

    @staticmethod
    def pasta_de_projetos() -> Path:
        docs = Path.home() / "Documents"
        if not docs.is_dir():
            docs = Path.home()
        return docs / "JARVIS Cowork"

    def novo_projeto(self, pedido: str) -> Path:
        """Pasta nova para um pedido sem projeto aberto ("faz um jogo...")."""
        t = _sem_acento(pedido).lower()
        m = re.search(r"\b(jogo|game|app|aplicativo|sistema|site|programa|painel|calculadora|loja|agenda)"
                      r"(?:\s+(?:de|da|do|dos|das|para|pra))?\s+([a-z0-9 ]{2,30})", t)
        base = f"{m.group(1)} {m.group(2)}" if m else "Projeto"
        base = re.sub(r"\b(um|uma|o|a|os|as|pra|para|mim|me|bem|bonito|bonita|simples|novo|nova)\b", " ", base)
        nome = re.sub(r"[^A-Za-z0-9 ]", "", base).strip().title() or "Projeto"
        nome = re.sub(r"\s+", " ", nome)[:40]
        raiz = self.pasta_de_projetos()
        alvo = raiz / nome
        n = 2
        while alvo.exists():
            alvo = raiz / f"{nome} {n}"
            n += 1
        alvo.mkdir(parents=True, exist_ok=True)
        return alvo

    # ── empacotar a pasta ───────────────────────────────────────────────────
    def _empacotar(self, extras: dict) -> bytes:
        assert self.pasta is not None
        raiz = self.pasta
        buf = io.BytesIO()
        grandes: list[str] = []
        total = 0
        qtd = 0
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for dirpath, dirnames, filenames in os.walk(raiz):
                self._checar_cancelado()
                dirnames[:] = sorted(d for d in dirnames
                                     if d not in _IGNORAR_DIRS
                                     and not os.path.islink(os.path.join(dirpath, d)))
                for nome in sorted(filenames):
                    if nome.lower() in _IGNORAR_ARQ:
                        continue
                    cheio = Path(dirpath) / nome
                    if cheio.is_symlink() or not cheio.is_file():
                        continue
                    rel = cheio.relative_to(raiz).as_posix()
                    if rel.lower().startswith("_jarvis_cowork/"):
                        continue
                    try:
                        tam = cheio.stat().st_size
                    except OSError:
                        continue
                    qtd += 1
                    if qtd > _MAX_ARQUIVOS:
                        raise RuntimeError(
                            f"essa pasta tem arquivos demais (mais de {_MAX_ARQUIVOS}). "
                            "Escolha a pasta só do projeto")
                    texto = cheio.suffix.lower() in _TEXTO or nome.lower() in (".gitignore",)
                    if (not texto and tam > _MAX_BINARIO) or total + tam > _MAX_ZIP * 0.9:
                        grandes.append(f"{rel} ({tam // 1024} KB)")
                        continue
                    try:
                        z.write(cheio, rel)
                    except (OSError, ValueError):
                        continue
                    total += tam
            if grandes:
                extras = dict(extras)
                extras["arquivos_grandes.txt"] = "\n".join(grandes[:300])
            for nome, conteudo in extras.items():
                if conteudo is None or conteudo == "" or conteudo == b"":
                    continue
                dado = conteudo.encode("utf-8") if isinstance(conteudo, str) else conteudo
                z.writestr(f"_jarvis_cowork/{nome}", dado)
        dados = buf.getvalue()
        if len(dados) > _MAX_ZIP:
            raise RuntimeError("o projeto compactado passou de 38 MB")
        return dados

    # ── desfaz ──────────────────────────────────────────────────────────────
    def _dir_pontos(self) -> Path:
        assert self.pasta is not None
        return self.pasta / PASTA_INTERNA / "desfazer"

    def _pontos(self) -> list[Path]:
        try:
            d = self._dir_pontos()
            return sorted((p for p in d.iterdir() if (p / "manifesto.json").is_file()),
                          key=lambda p: p.name)
        except Exception:
            return []

    def _novo_ponto(self, pedido: str) -> Path:
        d = self._dir_pontos() / _agora()
        n = 2
        while d.exists():
            d = d.with_name(f"{_agora()}-{n}")
            n += 1
        (d / "arquivos").mkdir(parents=True, exist_ok=True)
        (d / "manifesto.json").write_text(json.dumps(
            {"pedido": pedido[:300], "criados": [], "guardados": []}, ensure_ascii=False),
            encoding="utf-8")
        return d

    def _limpar_pontos(self) -> None:
        pontos = self._pontos()
        for p in pontos[:-_MAX_PONTOS]:
            shutil.rmtree(p, ignore_errors=True)

    def desfazer(self) -> str:
        if not self.pasta:
            return "Não tem projeto aberto no Cowork."
        if self._ocupado:
            return "Espera eu terminar o trabalho atual; depois eu desfaço."
        pontos = self._pontos()
        if not pontos:
            return "Não tem nada para desfazer neste projeto."
        ponto = pontos[-1]
        try:
            man = json.loads((ponto / "manifesto.json").read_text(encoding="utf-8"))
        except Exception:
            shutil.rmtree(ponto, ignore_errors=True)
            return "O ponto de desfazer estava estragado; apaguei ele."
        self._parar_processo()
        for rel in man.get("criados") or []:
            alvo = self.pasta / rel
            if _dentro(self.pasta, alvo) and alvo.is_file():
                try:
                    alvo.unlink()
                except OSError:
                    pass
        for rel in man.get("guardados") or []:
            copia = ponto / "arquivos" / rel
            alvo = self.pasta / rel
            if copia.is_file() and _dentro(self.pasta, alvo):
                alvo.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(copia, alvo)
        shutil.rmtree(ponto, ignore_errors=True)
        self._ultimo_teste = None
        self._avisar(etapa="")
        pedido = str(man.get("pedido") or "").strip()
        return ("Desfiz a última mudança do Cowork" + (f" ({pedido[:60]})" if pedido else "") + ".")

    # ── aplicar o que voltou da VPS ─────────────────────────────────────────
    def _aplicar(self, zip_path: Path, ponto: Path) -> dict:
        assert self.pasta is not None
        man_ponto = json.loads((ponto / "manifesto.json").read_text(encoding="utf-8"))
        criados = set(man_ponto.get("criados") or [])
        guardados = set(man_ponto.get("guardados") or [])

        def guardar(rel: str) -> None:
            if rel in guardados or rel in criados:
                return
            origem = self.pasta / rel
            if origem.is_file():
                destino = ponto / "arquivos" / rel
                destino.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(origem, destino)
                guardados.add(rel)
            else:
                criados.add(rel)

        alterados, removidos = [], []
        with zipfile.ZipFile(zip_path) as z:
            nomes = {}
            for info in z.infolist():
                if info.is_dir():
                    continue
                rel = _nome_seguro(_nome_do_zip(info))
                if rel:
                    nomes[rel] = info
            manifesto = {}
            for rel, info in list(nomes.items()):
                if rel.endswith("_jarvis_mudancas.json") and "/" not in rel:
                    try:
                        manifesto = json.loads(z.read(info).decode("utf-8", errors="replace"))
                    except Exception:
                        manifesto = {}
                    nomes.pop(rel)
            for rel, info in nomes.items():
                alvo = self.pasta / rel
                if not _dentro(self.pasta, alvo):
                    continue
                guardar(rel)
                alvo.parent.mkdir(parents=True, exist_ok=True)
                tmp = alvo.with_name(alvo.name + ".jarvis-tmp")
                with z.open(info) as src, open(tmp, "wb") as dst:
                    if alvo.suffix.lower() == ".json":
                        # BOM (que o PowerShell da VPS poe) quebra JSON.parse.
                        dado = src.read()
                        dst.write(dado[3:] if dado.startswith(b"\xef\xbb\xbf") else dado)
                    else:
                        shutil.copyfileobj(src, dst)
                # Antivirus/indexador as vezes segura o arquivo por um instante.
                for tentativa in range(5):
                    try:
                        os.replace(tmp, alvo)
                        break
                    except PermissionError:
                        if tentativa == 4:
                            raise RuntimeError(f"o arquivo {rel} esta aberto/travado por outro programa")
                        time.sleep(0.4)
                alterados.append(rel)
            for rel_bruto in manifesto.get("removidos") or []:
                rel = _nome_seguro(rel_bruto)
                if not rel:
                    continue
                alvo = self.pasta / rel
                if not _dentro(self.pasta, alvo) or not alvo.is_file():
                    continue
                guardar(rel)
                try:
                    alvo.unlink()
                    removidos.append(rel)
                except OSError:
                    pass
        man_ponto["criados"] = sorted(criados)
        man_ponto["guardados"] = sorted(guardados)
        (ponto / "manifesto.json").write_text(json.dumps(man_ponto, ensure_ascii=False), encoding="utf-8")
        return {"resumo": str(manifesto.get("resumo") or "").strip(),
                "alterados": alterados, "removidos": removidos,
                "criados": [r for r in alterados if r in criados]}

    # ── historico do projeto (contexto para o Codex) ────────────────────────
    def _historico(self) -> list:
        try:
            return json.loads((self.pasta / PASTA_INTERNA / "historico.json").read_text(encoding="utf-8"))
        except Exception:
            return []

    def _registrar(self, pedido: str, resumo: str) -> None:
        try:
            h = self._historico()
            h.append({"quando": time.strftime("%d/%m %H:%M"), "pedido": pedido[:400], "resumo": resumo[:400]})
            d = self.pasta / PASTA_INTERNA
            d.mkdir(parents=True, exist_ok=True)
            (d / "historico.json").write_text(json.dumps(h[-20:], ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception:
            pass

    def _historico_txt(self) -> str:
        linhas = []
        for item in self._historico()[-8:]:
            linhas.append(f"- [{item.get('quando', '')}] pedido: {item.get('pedido', '')}"
                          f" | feito: {item.get('resumo', '')}")
        return "\n".join(linhas)

    # ── como rodar o projeto ────────────────────────────────────────────────
    def como_rodar(self) -> dict | None:
        if not self.pasta:
            return None
        cfg = None
        try:
            bruto = (self.pasta / ARQ_COMO_RODAR).read_text(encoding="utf-8-sig")
            cfg = json.loads(bruto)
        except Exception:
            cfg = None
        if isinstance(cfg, dict):
            tipo = str(cfg.get("tipo") or "").lower()
            if tipo == "web":
                abrir = _nome_seguro(str(cfg.get("abrir") or "index.html")) or "index.html"
                if (self.pasta / abrir).is_file():
                    return {"tipo": "web", "abrir": abrir}
            if tipo == "comando" and str(cfg.get("comando") or "").strip():
                return {"tipo": "comando", "comando": str(cfg["comando"]).strip()[:300],
                        "instalar": str(cfg.get("instalar") or "").strip()[:300]}
        # Sem o arquivo: adivinha pelo que existe na pasta.
        if (self.pasta / "index.html").is_file():
            return {"tipo": "web", "abrir": "index.html"}
        pkg = self.pasta / "package.json"
        if pkg.is_file():
            try:
                scripts = json.loads(pkg.read_text(encoding="utf-8-sig")).get("scripts") or {}
            except Exception:
                scripts = {}
            for s in ("dev", "start"):
                if s in scripts:
                    return {"tipo": "comando", "comando": f"npm run {s}" if s == "dev" else "npm start",
                            "instalar": "npm install"}
        for nome in ("main.py", "app.py", "jogo.py", "game.py", "run.py"):
            if (self.pasta / nome).is_file():
                req = "pip install -r requirements.txt" if (self.pasta / "requirements.txt").is_file() else ""
                return {"tipo": "comando", "comando": f"python {nome}", "instalar": req}
        htmls = sorted(self.pasta.glob("*.html"))
        if htmls:
            return {"tipo": "web", "abrir": htmls[0].name}
        return None

    # ── servidor local (pagina com modulos JS nao abre por file://) ─────────
    def _url_web(self, abrir: str) -> str:
        if self._servidor is None or self._servidor_pasta != self.pasta:
            self._parar_servidor()
            import functools
            from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

            class _Silencioso(SimpleHTTPRequestHandler):
                def log_message(self, *a):
                    pass

                def end_headers(self):
                    # sempre a versao nova depois de cada mudanca
                    self.send_header("Cache-Control", "no-store")
                    super().end_headers()

            handler = functools.partial(_Silencioso, directory=str(self.pasta))
            srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            srv.daemon_threads = True
            threading.Thread(target=srv.serve_forever, daemon=True, name="cowork-web").start()
            self._servidor = srv
            self._servidor_pasta = self.pasta
            self._servidor_porta = srv.server_address[1]
        from urllib.parse import quote
        return f"http://127.0.0.1:{self._servidor_porta}/{quote(abrir)}"

    def _parar_servidor(self) -> None:
        srv = self._servidor
        self._servidor = None
        self._servidor_pasta = None
        if srv is not None:
            try:
                srv.shutdown()
                srv.server_close()
            except Exception:
                pass

    # ── teste: roda escondido, junta erros e a foto da tela ─────────────────
    def testar(self) -> dict:
        cfg = self.como_rodar()
        if not cfg:
            return {"tipo": "nenhum", "erros": [], "tela": None,
                    "relatorio": "Ainda nao ha como rodar o projeto (nenhum index.html nem comando)."}
        try:
            if cfg["tipo"] == "web":
                r = self._testar_web(self._url_web(cfg["abrir"]))
            else:
                r = self._testar_comando(cfg)
        except CoworkCancelado:
            raise
        except Exception as exc:
            r = {"tipo": cfg["tipo"], "erros": [], "tela": None,
                 "relatorio": f"O teste automatico nao conseguiu rodar: {type(exc).__name__}: {str(exc)[:300]}"}
        self._ultimo_teste = r
        self._ultimo_teste_em = time.time()
        return r

    @staticmethod
    def _tela_lisa(jpeg: bytes | None) -> bool:
        if not jpeg:
            return False
        try:
            from PIL import Image, ImageStat
            img = Image.open(io.BytesIO(jpeg)).convert("L").resize((64, 40))
            return ImageStat.Stat(img).stddev[0] < 4.0
        except Exception:
            return False

    def _testar_web(self, url: str, tempo: float = 3.0) -> dict:
        self._avisar(etapa="testando no navegador (escondido)")
        pw_dir = BASE_DIR / "runtime" / "playwright"
        if pw_dir.is_dir():
            os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(pw_dir))
        from playwright.sync_api import sync_playwright
        erros, avisos, falhas = [], [], []
        interacao: list[str] = []
        tela = None
        texto = ""
        with sync_playwright() as pw:
            navegador = None
            for opcoes in ({}, {"channel": "msedge"}, {"channel": "chrome"}):
                try:
                    navegador = pw.chromium.launch(headless=True, **opcoes)
                    break
                except Exception:
                    continue
            if navegador is None:
                raise RuntimeError("nenhum navegador disponivel para o teste")
            try:
                pagina = navegador.new_page(viewport={"width": 1280, "height": 800})
                pagina.on("pageerror", lambda e: erros.append(f"erro de JavaScript: {str(e)[:300]}"))

                def _console(msg):
                    try:
                        if msg.type == "error":
                            erros.append(f"console.error: {msg.text[:300]}")
                        elif msg.type == "warning" and len(avisos) < 10:
                            avisos.append(f"console.warn: {msg.text[:200]}")
                    except Exception:
                        pass
                pagina.on("console", _console)

                def _resposta(resp):
                    try:
                        if resp.status >= 400 and not resp.url.endswith("favicon.ico"):
                            falhas.append(f"{resp.status} {resp.url.split('/', 3)[-1][:120]}")
                    except Exception:
                        pass
                pagina.on("response", _resposta)
                pagina.on("requestfailed", lambda r: falhas.append(
                    f"falhou {r.url.split('/', 3)[-1][:120]}") if not r.url.endswith("favicon.ico") else None)
                pagina.goto(url, wait_until="load", timeout=25000)
                pagina.wait_for_timeout(int(tempo * 1000))
                self._checar_cancelado()
                tela = pagina.screenshot(type="jpeg", quality=72)
                try:
                    texto = pagina.inner_text("body", timeout=3000)[:1200]
                except Exception:
                    texto = ""
                # USAR UM POUCO, como gente: clica no primeiro botao (o "Comecar"
                # de um jogo, por exemplo) e aperta umas teclas. Erro que so
                # aparece com o jogo rodando passaria batido so com a tela inicial.
                antes_uso = len(erros)
                try:
                    botao = pagina.locator("button:visible, [role=button]:visible, .btn:visible").first
                    if botao.count():
                        botao.click(timeout=2000)
                        interacao.append("cliquei no primeiro botao")
                except Exception:
                    pass
                try:
                    for tecla in ("Enter", "ArrowRight", "ArrowUp", "Space"):
                        pagina.keyboard.press(tecla)
                        pagina.wait_for_timeout(250)
                    interacao.append("apertei Enter, setas e espaco")
                    pagina.wait_for_timeout(1800)
                    tela_uso = pagina.screenshot(type="jpeg", quality=72)
                except Exception:
                    tela_uso = None
                if len(erros) > antes_uso:
                    interacao.append(f"{len(erros) - antes_uso} erro(s) apareceram DEPOIS de usar")
                tela = _lado_a_lado(tela, tela_uso)
            finally:
                try:
                    navegador.close()
                except Exception:
                    pass
        partes = [f"Teste automatico no navegador (Chromium sem janela, 1280x800) em {url}."]
        if interacao:
            partes.append("Depois de abrir: " + "; ".join(interacao) + ". A imagem mostra lado a lado "
                          "a tela inicial (esquerda) e a tela depois de usar (direita).")
        partes.append("Erros: " + ("; ".join(erros[:12]) if erros else "nenhum."))
        if falhas:
            partes.append("Arquivos/recursos que nao carregaram: " + "; ".join(falhas[:12]))
        if avisos:
            partes.append("Avisos do console: " + "; ".join(avisos[:6]))
        if self._tela_lisa(tela):
            partes.append("ATENCAO: a tela capturada esta praticamente lisa (pode estar em branco).")
        partes.append("Texto visivel na pagina: " + (re.sub(r"\s+", " ", texto).strip()[:800] or "(nenhum)"))
        return {"tipo": "web", "erros": erros + falhas, "tela": tela, "relatorio": "\n".join(partes), "url": url}

    # ── projetos que rodam por comando (python, node...) ────────────────────
    _PERMITIDOS = {"python", "python3", "py", "pythonw", "node", "npm", "npx", "pnpm", "yarn",
                   "deno", "bun", "dotnet", "java", "go", "cargo", "flutter", "love", "godot", "ruby", "php"}

    def _python(self) -> tuple[list, bool]:
        """(comando do python, e o python embutido do JARVIS?)."""
        py = shutil.which("py")
        if py:
            return [py, "-3"], False
        for cand in [shutil.which("python")] + [shutil.which("python3")]:
            if cand and "windowsapps" not in cand.lower():
                return [cand], False
        return [str(BASE_DIR / "runtime" / "python" / "python.exe")], True

    def _montar_comando(self, comando: str) -> tuple[str, dict, str]:
        """Comando seguro + ambiente. Devolve ("", env, motivo) se recusar."""
        cmd = comando.strip()
        if re.search(r"[&|;<>`]|\b(del|erase|rd|rmdir|format|shutdown|reg|takeown|icacls|"
                     r"remove-item|invoke-webrequest|curl|wget)\b", cmd, re.I):
            return "", {}, "o comando tinha partes perigosas"
        primeiro = cmd.split()[0].lower() if cmd.split() else ""
        primeiro_base = Path(primeiro).name.lower().removesuffix(".exe").removesuffix(".cmd")
        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        node_dir = BASE_DIR / "runtime" / "node"
        if node_dir.is_dir():
            env["PATH"] = env.get("PATH", "") + os.pathsep + str(node_dir)
        if primeiro_base in ("python", "python3", "py", "pythonw"):
            exe, embutido = self._python()
            resto = cmd.split(None, 1)[1] if len(cmd.split()) > 1 else ""
            if primeiro_base == "py":
                resto = re.sub(r"^-3(\.\d+)?\s*", "", resto)
            cmd = subprocess.list2cmdline(exe) + (" " + resto if resto else "")
            if embutido:
                libs = self.pasta / PASTA_INTERNA / "pylibs"
                env["PYTHONPATH"] = str(libs) + os.pathsep + env.get("PYTHONPATH", "")
            return cmd, env, ""
        if primeiro_base in self._PERMITIDOS:
            # O programa existe neste PC? Sem ele, nao e defeito do projeto (e
            # nao vale gastar rodada de correcao): e so avisar o que falta.
            achou = shutil.which(primeiro_base, path=env.get("PATH"))
            if not achou:
                return "", env, (f"o {primeiro_base} nao esta instalado neste computador "
                                 f"(precisa instalar para rodar este projeto)")
            return cmd, env, ""
        # .bat/.cmd/.ps1 NAO rodam sozinhos: seriam qualquer comando que o
        # servidor escrevesse, executado no PC do dono sem ele ver.
        return "", env, f"o comando '{primeiro}' nao esta na lista dos que eu rodo sozinho"

    def _instalar(self, cfg: dict, env: dict) -> str:
        """Instala dependencias quando o arquivo delas mudou. Devolve o relato."""
        inst = str(cfg.get("instalar") or "").strip()
        if not inst:
            return ""
        alvo = None
        for nome in ("package.json", "requirements.txt", "pyproject.toml"):
            if (self.pasta / nome).is_file():
                alvo = self.pasta / nome
                break
        marca = self.pasta / PASTA_INTERNA / "instalado.json"
        assinatura = hashlib.sha1((alvo.read_bytes() if alvo else b"") + inst.encode()).hexdigest()
        try:
            if json.loads(marca.read_text(encoding="utf-8")).get("assinatura") == assinatura:
                return ""
        except Exception:
            pass
        m = re.match(r"^(?:(?:python3?|py)(?:\s+-3(?:\.\d+)?)?\s+-m\s+)?pip3?\s+install\s+(.+)$", inst, re.I)
        if m:
            # pip: sempre pelo python que vai RODAR o projeto. Se for o embutido
            # do JARVIS, instala numa pasta do projeto (--target) para nao mexer
            # no proprio JARVIS.
            if re.search(r"[&|;<>`]", m.group(1)):
                return "Nao instalei as dependencias: o comando tinha partes perigosas."
            exe, embutido = self._python()
            args = exe + ["-m", "pip", "install", "--disable-pip-version-check"]
            if embutido:
                args += ["--target", str(self.pasta / PASTA_INTERNA / "pylibs")]
            cmd = subprocess.list2cmdline(args + m.group(1).split())
            env2 = dict(env)
        else:
            cmd, env2, motivo = self._montar_comando(inst)
            if not cmd:
                return f"Nao instalei as dependencias: {motivo}."
        self._avisar(etapa="instalando as dependências do projeto")
        # Popen + espera em pedacos: o "parar" funciona no meio da instalacao
        # (um npm install pode levar minutos).
        log = self.pasta / PASTA_INTERNA / "instalacao.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "wb") as saida_arq:
            proc = subprocess.Popen(cmd, cwd=str(self.pasta), shell=True, env=env2, stdout=saida_arq,
                                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            fim = time.time() + 900
            try:
                while proc.poll() is None:
                    if self._cancelar.is_set():
                        _matar_arvore(proc.pid)
                        raise CoworkCancelado()
                    if time.time() > fim:
                        _matar_arvore(proc.pid)
                        return f"A instalacao ({inst}) passou de 15 minutos e foi parada."
                    time.sleep(0.5)
            finally:
                if proc.poll() is None:
                    _matar_arvore(proc.pid)
        try:
            saida = log.read_text(encoding="utf-8", errors="replace")
        except Exception:
            saida = ""
        if proc.returncode == 0:
            marca.write_text(json.dumps({"assinatura": assinatura}), encoding="utf-8")
            return ""
        return f"A instalacao ({inst}) falhou (codigo {proc.returncode}):\n" + saida[-3000:]

    def _testar_comando(self, cfg: dict) -> dict:
        cmd, env, motivo = self._montar_comando(cfg["comando"])
        if not cmd:
            return {"tipo": "comando", "erros": [], "tela": None,
                    "relatorio": f"Nao rodei o projeto: {motivo}. Comando: {cfg['comando']}"}
        relato_inst = self._instalar(cfg, env)
        self._checar_cancelado()
        self._avisar(etapa="rodando o projeto para testar")
        log = self.pasta / PASTA_INTERNA / "ultimo_teste.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        erros: list[str] = []
        tela = None
        url = ""
        with open(log, "wb") as saida:
            proc = subprocess.Popen(cmd, cwd=str(self.pasta), shell=True, env=env, stdout=saida,
                                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                fim = time.time() + 12.0
                while time.time() < fim and proc.poll() is None:
                    self._checar_cancelado()
                    time.sleep(0.4)
                    try:
                        m = re.search(r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0):\d+[^\s'\"]*",
                                      log.read_text(encoding="utf-8", errors="replace"))
                    except Exception:
                        m = None
                    if m:
                        url = m.group(0).replace("0.0.0.0", "127.0.0.1")
                        time.sleep(1.5)
                        break
                codigo = proc.poll()
                if codigo is None:
                    if url:
                        web = self._testar_web(url)
                        erros += web["erros"]
                        tela = web["tela"]
                    else:
                        time.sleep(2.0)
                        tela = _foto_das_janelas(_arvore(proc.pid))
            finally:
                codigo = proc.poll()
                _matar_arvore(proc.pid)
        try:
            texto = log.read_text(encoding="utf-8", errors="replace")
        except Exception:
            texto = ""
        cauda = "\n".join(texto.strip().splitlines()[-80:])[-6000:]
        if codigo not in (None, 0):
            erros.append(f"o programa fechou sozinho com codigo {codigo}")
        for padrao in (r"Traceback \(most recent call last\)", r"\b\w*Error:", r"Cannot find module",
                       r"ModuleNotFoundError", r"SyntaxError", r"Unhandled", r"Exception in"):
            if re.search(padrao, texto):
                erros.append(f"a saida tem erro ({padrao.strip(chr(92))})")
                break
        partes = [f"Teste automatico rodando: {cfg['comando']}"]
        if relato_inst:
            partes.append(relato_inst)
            erros.append("a instalacao das dependencias falhou")
        partes.append("Situacao: " + ("continuou rodando (normal para app/jogo/servidor)" if codigo is None
                                      else f"terminou com codigo {codigo}"))
        if url:
            partes.append(f"Endereco que ele abriu: {url}")
        partes.append("Saida do programa (fim):\n" + (cauda or "(nada)"))
        if tela is None and codigo is None and not url:
            partes.append("Nao encontrei janela do programa para fotografar.")
        if self._tela_lisa(tela):
            partes.append("ATENCAO: a janela capturada esta praticamente lisa (pode estar em branco).")
        return {"tipo": "comando", "erros": erros, "tela": tela, "relatorio": "\n".join(partes), "url": url}

    # ── abrir para o dono ver ───────────────────────────────────────────────
    def _parar_processo(self) -> None:
        proc = self._processo
        self._processo = None
        if proc is not None and proc.poll() is None:
            _matar_arvore(proc.pid)
        self._fechar_janela_app()

    @staticmethod
    def _navegador() -> str | None:
        """Edge ou Chrome instalados (para abrir o projeto como PROGRAMA)."""
        candidatos = [
            os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
            shutil.which("msedge") or "", shutil.which("chrome") or "",
        ]
        for c in candidatos:
            if c and os.path.isfile(c):
                return c
        return None

    def _fechar_janela_app(self) -> None:
        janela = getattr(self, "_janela_app", None)
        self._janela_app = None
        if janela is not None and janela.poll() is None:
            _matar_arvore(janela.pid)

    def _abrir_janela_app(self, url: str) -> bool:
        """JANELA PROPRIA, sem barra de endereco nem abas (05/10/2026).

        O Diego pediu um jogo e ele abriu numa aba do Chrome dele, no meio das
        abas pessoais — "ele criou no navegador". Agora o projeto abre como um
        programa: Edge/Chrome no modo --app, com um perfil SO do Cowork (nao
        mexe no navegador do dono). A cada mudanca a janela velha fecha e a
        nova abre ja com a versao atualizada."""
        exe = self._navegador()
        if not exe:
            return False
        self._fechar_janela_app()
        perfil = BASE_DIR / "cache" / "cowork_janela"
        try:
            perfil.mkdir(parents=True, exist_ok=True)
            self._janela_app = subprocess.Popen(
                [exe, f"--app={url}", f"--user-data-dir={perfil}", "--window-size=1280,860",
                 "--no-first-run", "--no-default-browser-check", "--disable-features=Translate",
                 "--autoplay-policy=no-user-gesture-required"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception:
            self._janela_app = None
            return False

    def rodar(self) -> str:
        """Abre o projeto para o dono ver — pedido pelo dono (botao/voz)."""
        if self._ocupado:
            return "Espera eu terminar o trabalho do Cowork; no fim eu mesmo abro para você."
        return self._abrir_resultado()

    def _abrir_resultado(self) -> str:
        """Abre o projeto para o dono ver (janela propria ou o programa)."""
        if not self.pasta:
            return "Não tem projeto aberto no Cowork."
        cfg = self.como_rodar()
        if not cfg:
            return "Esse projeto ainda não tem o que abrir."
        if cfg["tipo"] == "web":
            url = self._url_web(cfg["abrir"])
            if self._abrir_janela_app(url):
                return "Abri o projeto numa janela própria."
            try:
                if os.name == "nt":
                    os.startfile(url)  # noqa: S606
                else:
                    subprocess.Popen(["xdg-open", url])
                return "Abri o projeto no navegador."
            except Exception as exc:
                return f"Não consegui abrir o navegador: {str(exc)[:80]}"
        cmd, env, motivo = self._montar_comando(cfg["comando"])
        if not cmd:
            return f"Não rodei o projeto: {motivo}."
        self._parar_processo()
        console = bool(self._ultimo_teste and self._ultimo_teste.get("tipo") == "comando"
                       and self._ultimo_teste.get("tela") is None
                       and not self._ultimo_teste.get("url"))
        flags = (getattr(subprocess, "CREATE_NEW_CONSOLE", 0) if console
                 else getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            self._processo = subprocess.Popen(cmd, cwd=str(self.pasta), shell=True, env=env,
                                              creationflags=flags)
        except Exception as exc:
            return f"Não consegui abrir o projeto: {str(exc)[:80]}"
        url = (self._ultimo_teste or {}).get("url") or ""
        if url:
            time.sleep(2.5)
            if not self._abrir_janela_app(url):
                try:
                    os.startfile(url)  # noqa: S606
                except Exception:
                    pass
        return "Abri o projeto para você ver."

    def abrir_pasta_no_explorer(self) -> str:
        if not self.pasta:
            return "Não tem projeto aberto no Cowork."
        try:
            os.startfile(str(self.pasta))  # noqa: S606
            return "Abri a pasta do projeto."
        except Exception as exc:
            return f"Não consegui abrir a pasta: {str(exc)[:80]}"

    # ── o pedido ────────────────────────────────────────────────────────────
    def ocupado(self) -> bool:
        return self._ocupado

    def parar(self) -> str:
        if not self._ocupado:
            return "O Cowork não está trabalhando agora."
        self._cancelar.set()
        self._avisar(etapa="parando...")
        return "Certo, estou parando o trabalho do Cowork."

    def _rodada(self, ponte, pedido: str, modo: str, teste: dict | None, ponto: Path) -> dict:
        extras = {"historico.txt": self._historico_txt()}
        if teste:
            extras["relatorio.txt"] = teste.get("relatorio") or ""
            if teste.get("tela"):
                extras["tela.jpg"] = teste["tela"]
        self._avisar(etapa="preparando a cópia do projeto")
        dados = self._empacotar(extras)
        self._checar_cancelado()
        self._avisar(etapa=("conferindo o teste com o servidor" if modo == "revisao"
                            else "trabalhando no servidor"))
        r = ponte.cowork_pela_ponte(
            dados, pedido, self.pasta.name, modo, timeout=_TIMEOUT_RODADA,
            ao_progresso=lambda t: self._avisar(linha=t), cancelar=self._cancelar)
        self._checar_cancelado()
        caminho = Path(r["zip"])
        try:
            self._avisar(etapa="aplicando as mudanças na pasta")
            aplicado = self._aplicar(caminho, ponto)
        finally:
            try:
                caminho.unlink()
            except OSError:
                pass
        if not aplicado["resumo"]:
            aplicado["resumo"] = str(r.get("resumo") or "").strip()
        return aplicado

    def executar_pedido(self, pedido: str, ponte) -> str:
        """Faz o pedido inteiro. BLOQUEIA (minutos): chamar numa thread."""
        pedido = str(pedido or "").strip()
        if not pedido:
            return "Diga o que você quer que eu faça no projeto."
        if ponte is None:
            return "O Cowork precisa da conexão com o servidor do JARVIS, e ela não está ativa agora."
        with self._trava:
            if self._ocupado:
                return ("Ainda estou trabalhando no pedido anterior do Cowork. "
                        "Assim que terminar eu te aviso.")
            self._ocupado = True
        self._cancelar.clear()
        self.linhas = []
        aviso_pasta = ""
        try:
            if not self.pasta:
                nova = self.novo_projeto(pedido)
                self.pasta = nova
                self._salvar_config()
                aviso_pasta = f"Criei o projeto na pasta {nova.name}, dentro de Documentos, JARVIS Cowork. "
            self._avisar(etapa="começando")
            self._limpar_pontos()
            ponto = self._novo_ponto(pedido)

            # Como o projeto esta AGORA (para "a cor esta errada" fazer sentido).
            teste_antes = None
            if self.como_rodar():
                teste_antes = self.testar()
                self._checar_cancelado()

            # PASTA VAZIA = PROJETO NOVO, dito com todas as letras (05/10/2026).
            # Numa pasta vazia o primeiro comando do Codex (listar arquivos)
            # volta vazio com codigo 1, e o modelo chegou a concluir "estou em
            # modo somente leitura" e devolver sem fazer nada.
            vazio = not any(p.name != PASTA_INTERNA for p in self.pasta.iterdir())
            descricao = pedido
            if vazio:
                descricao = ("[PROJETO NOVO: esta pasta esta VAZIA. Crie todos os arquivos do zero "
                             "nela. Voce TEM permissao de escrita; listagem vazia ou 'rg' com codigo 1 "
                             "so quer dizer que ainda nao ha arquivos.]\n" + pedido)
            feito = self._rodada(ponte, descricao, "trabalho", teste_antes, ponto)
            if (not feito["alterados"] and not feito["removidos"]
                    and _ACHOU_QUE_NAO_PODIA.search(feito["resumo"] or "")):
                self._avisar(etapa="o servidor se confundiu; mandando de novo")
                feito = self._rodada(ponte, (
                    "[ATENCAO: na tentativa anterior voce concluiu que nao tinha acesso de escrita. "
                    "Isso estava ERRADO: a pasta aceita escrita normalmente. Faca o pedido agora, "
                    "criando e alterando os arquivos de verdade.]\n" + descricao),
                    "trabalho", teste_antes, ponto)
            if not feito["alterados"] and not feito["removidos"]:
                # Nada mudou: so repassa o que o servidor disse, sem testar,
                # sem abrir, sem registrar no historico e sem deixar um
                # "desfaz" vazio para tras.
                shutil.rmtree(ponto, ignore_errors=True)
                return feito["resumo"] or "O servidor terminou sem mudar nada no projeto."
            resumo = feito["resumo"] or "Terminei o que você pediu."
            mudou_muito = bool(feito["criados"]) or len(feito["alterados"]) >= 3

            correcoes = 0
            teste = None
            for rodada in range(_RODADAS_EXTRAS):
                self._checar_cancelado()
                teste = self.testar()
                if teste["tipo"] == "nenhum":
                    break
                visual = rodada == 0 and mudou_muito and teste.get("tela") is not None
                if not teste["erros"] and not visual:
                    break
                self._avisar(etapa=("corrigindo o que o teste encontrou" if teste["erros"]
                                    else "conferindo a tela do resultado"))
                rev = self._rodada(ponte, pedido, "revisao", teste, ponto)
                if not rev["alterados"] and not rev["removidos"]:
                    break                       # o Codex olhou e disse que esta OK
                correcoes += 1
                teste = None                    # precisa testar de novo

            if teste is None and self.como_rodar():
                teste = self.testar()
            self._registrar(pedido, resumo)
            self._checar_cancelado()
            abriu = ""
            if self.como_rodar():
                self._avisar(etapa="abrindo para você ver")
                abriu = self._abrir_resultado()

            frase = aviso_pasta + resumo
            if correcoes:
                frase += (" Testei, encontrei o que precisava ajustar e corrigi."
                          if correcoes == 1 else f" Testei e fiz {correcoes} rodadas de correção.")
            if teste and teste.get("erros"):
                frase += " Ainda apareceu um erro no teste; se quiser, peça para eu corrigir."
            if abriu.startswith("Abri"):
                frase += " Já abri para você ver."
            return frase
        except CoworkCancelado:
            return "Parei o trabalho do Cowork. O que já tinha sido aplicado pode ser desfeito com 'desfaz'."
        except Exception as exc:
            msg = re.sub(r"(?i)codex|openai", "JARVIS", str(exc).strip())[:220]
            if msg.lower() == "cancelado":
                return "Parei o trabalho do Cowork."
            return f"O Cowork não conseguiu concluir: {msg}"
        finally:
            self._ocupado = False
            self._avisar(etapa="")


def _lado_a_lado(a: bytes | None, b: bytes | None) -> bytes | None:
    """Junta duas fotos (antes e depois de usar) numa so, para o Codex ver as duas."""
    if not a or not b:
        return a or b
    try:
        from PIL import Image
        ia = Image.open(io.BytesIO(a)).convert("RGB")
        ib = Image.open(io.BytesIO(b)).convert("RGB")
        h = max(ia.height, ib.height)
        junta = Image.new("RGB", (ia.width + ib.width + 8, h), (40, 40, 40))
        junta.paste(ia, (0, 0))
        junta.paste(ib, (ia.width + 8, 0))
        junta.thumbnail((1800, 900))
        saida = io.BytesIO()
        junta.save(saida, format="JPEG", quality=72)
        return saida.getvalue()
    except Exception:
        return a


# ── utilidades de processo e janela (Windows) ──────────────────────────────
def _arvore(pid: int) -> list[int]:
    try:
        import psutil
        p = psutil.Process(pid)
        return [pid] + [c.pid for c in p.children(recursive=True)]
    except Exception:
        return [pid]


def _matar_arvore(pid: int) -> None:
    try:
        import psutil
        p = psutil.Process(pid)
        filhos = p.children(recursive=True)
        for c in filhos:
            try:
                c.kill()
            except Exception:
                pass
        try:
            p.kill()
        except Exception:
            pass
        psutil.wait_procs(filhos + [p], timeout=3)
    except Exception:
        pass


def _foto_das_janelas(pids: list[int]) -> bytes | None:
    """Foto da maior janela visivel desses processos (PrintWindow: funciona
    mesmo com a janela atras de outras)."""
    if os.name != "nt" or not pids:
        return None
    try:
        import ctypes
        from ctypes import wintypes
        from PIL import Image
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        alvo = set(pids)
        achadas = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        def _cada(h, _):
            if not user32.IsWindowVisible(h):
                return True
            dono = wintypes.DWORD()
            user32.GetWindowThreadProcessId(h, ctypes.byref(dono))
            if dono.value in alvo:
                r = wintypes.RECT()
                user32.GetWindowRect(h, ctypes.byref(r))
                area = (r.right - r.left) * (r.bottom - r.top)
                if area > 40000:
                    achadas.append((area, h, r.right - r.left, r.bottom - r.top))
            return True
        user32.EnumWindows(_cada, 0)
        if not achadas:
            return None
        achadas.sort(reverse=True)
        _, hwnd, w, h = achadas[0]
        hdc = user32.GetWindowDC(hwnd)
        mdc = gdi32.CreateCompatibleDC(hdc)
        bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
        gdi32.SelectObject(mdc, bmp)
        user32.PrintWindow(hwnd, mdc, 2)

        class _BIH(ctypes.Structure):
            _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                        ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                        ("biXPelsPerMeter", ctypes.c_long), ("biYPelsPerMeter", ctypes.c_long),
                        ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]
        bi = _BIH(ctypes.sizeof(_BIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = ctypes.create_string_buffer(w * h * 4)
        gdi32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mdc)
        user32.ReleaseDC(hwnd, hdc)
        img = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")
        img.thumbnail((1280, 900))
        saida = io.BytesIO()
        img.save(saida, format="JPEG", quality=75)
        return saida.getvalue()
    except Exception:
        return None


# Um Cowork por JARVIS.
COWORK = Cowork()
