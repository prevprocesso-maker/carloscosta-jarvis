"""Client-side adapter for the private VPS Codex worker."""

import os
import re
import subprocess
import sys
from pathlib import Path


def _relay(player):
    return getattr(player, "_codex_relay", None) if player is not None else None


# Formatos que o cliente pode arrastar para a janela e pedir para editar.
_IMAGENS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}


def _achar_imagem(params: dict, player) -> Path | None:
    """A foto a editar: a que o cliente indicou, ou a que esta na janela.

    O cliente pode arrastar o arquivo para o JARVIS, escolher pelo botao de
    procurar, ou simplesmente dizer o caminho. Os tres caem aqui.
    """
    candidatos = [
        params.get("imagem"), params.get("image"), params.get("arquivo"),
        params.get("path"), params.get("file"),
    ]
    for bruto in candidatos:
        if not bruto:
            continue
        caminho = Path(str(bruto).strip().strip('"').strip("'")).expanduser()
        if caminho.is_file() and caminho.suffix.lower() in _IMAGENS:
            return caminho
    # Nada indicado: usa o arquivo que esta carregado na janela do JARVIS.
    atual = getattr(player, "current_file", None) if player is not None else None
    if atual:
        caminho = Path(str(atual))
        if caminho.is_file() and caminho.suffix.lower() in _IMAGENS:
            return caminho
    return None


def _preparar_imagem(caminho: Path) -> bytes:
    """Deixa a foto em pe, reduz e devolve pronta para enviar.

    Este trecho e deliberadamente leve. O JARVIS esta falando enquanto isso, e
    trabalho pesado aqui rouba processamento da voz e faz o audio picotar. Por
    isso: nada de PNG com "optimize" (custa segundos de CPU), o JPEG entra no
    lugar sempre que a imagem nao tem transparencia, e o proprio decodificador
    ja abre a foto reduzida quando da.

    A rotacao vem do EXIF; sem aplicar, a foto chega deitada.
    """
    from io import BytesIO
    from PIL import Image, ImageOps

    ALVO = 1400

    with Image.open(caminho) as img:
        # Para JPEG, o draft() manda o decodificador ja entregar a imagem menor.
        # E muito mais rapido do que abrir em tamanho cheio e so depois reduzir.
        try:
            img.draft("RGB", (ALVO, ALVO))
        except Exception:
            pass

        img = ImageOps.exif_transpose(img)      # poe a foto em pe

        tem_transparencia = img.mode in ("RGBA", "LA", "P")
        if not tem_transparencia and img.mode != "RGB":
            img = img.convert("RGB")

        maior = max(img.size)
        if maior > ALVO:
            escala = ALVO / float(maior)
            novo = (max(1, int(img.width * escala)), max(1, int(img.height * escala)))
            # BILINEAR e bem mais barato que LANCZOS e a diferenca nao aparece
            # no resultado, porque a imagem sera redesenhada do outro lado.
            img = img.resize(novo, Image.BILINEAR)

        saida = BytesIO()
        if tem_transparencia:
            if img.mode != "RGBA":
                img = img.convert("RGBA")
            img.save(saida, format="PNG")        # sem optimize: rapido
        else:
            img.save(saida, format="JPEG", quality=88)
        return saida.getvalue()


def _contar_empresas(path: Path) -> int:
    """Conta os blocos numerados [1], [2], ... da lista de leads."""
    try:
        texto = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return 0
    return len(re.findall(r"(?m)^\s*\[\d+\]", texto))


# Pastas conhecidas pelo nome que o cliente costuma usar.
def _pastas_conhecidas() -> dict:
    casa = Path.home()
    return {
        "area de trabalho": casa / "Desktop",
        "área de trabalho": casa / "Desktop",
        "areadetrabalho": casa / "Desktop",
        "desktop": casa / "Desktop",
        "documentos": casa / "Documents",
        "documento": casa / "Documents",
        "documents": casa / "Documents",
        "downloads": casa / "Downloads",
        "download": casa / "Downloads",
        "imagens": casa / "Pictures",
        "pictures": casa / "Pictures",
        "musicas": casa / "Music",
        "músicas": casa / "Music",
        "videos": casa / "Videos",
        "vídeos": casa / "Videos",
    }


def _resolver_pasta(nome: str) -> Path | None:
    """Traduz o que o cliente falou em um caminho real dentro do perfil dele."""
    bruto = str(nome or "").strip().strip(chr(34)).strip("'")
    if not bruto:
        return None
    # tira possessivos ("minha area de trabalho" -> "area de trabalho")
    chave = re.sub(r"^(minhas?|meus?|na|no|em|para|pra)\s+", "", bruto.lower()).strip()
    chave = re.sub(r"^(minhas?|meus?|na|no|em)\s+", "", chave).strip()
    conhecidas = _pastas_conhecidas()
    if chave in conhecidas:
        return conhecidas[chave]
    # "pasta X" -> subpasta da Area de Trabalho, preservando o nome como o cliente escreveu
    m = re.match(r"^(?:na\s+|em\s+)?pasta\s+(.+)$", chave)
    if m:
        bruta = re.search(r"(?i)pasta\s+(.+)$", bruto)
        nome_pasta = (bruta.group(1) if bruta else m.group(1)).strip().strip(chr(34)).strip("'")
        return Path.home() / "Desktop" / nome_pasta
    caminho = Path(bruto).expanduser()
    if caminho.is_absolute():
        # nunca escrever em area de sistema
        baixo = str(caminho).lower().replace(chr(92), "/")
        proibidas = ("c:/windows", "c:/program files", "c:/programdata")
        if baixo.startswith(proibidas):
            return None
        return caminho
    return Path.home() / "Desktop" / bruto


def _entregar_copias(origem: Path, destinos, log=None) -> list:
    """Copia o resultado para cada pasta que o cliente pediu."""
    import shutil
    entregues = []
    for destino in destinos or []:
        pasta = _resolver_pasta(destino)
        if pasta is None:
            continue
        try:
            pasta.mkdir(parents=True, exist_ok=True)
            alvo = pasta / origem.name
            n = 1
            while alvo.exists() and alvo.resolve() != origem.resolve():
                alvo = pasta / f"{origem.stem} ({n}){origem.suffix}"
                n += 1
            if alvo.resolve() != origem.resolve():
                shutil.copy2(origem, alvo)
            entregues.append(alvo)
        except Exception as exc:
            if log:
                log(f"[JARVIS] Nao consegui salvar em {pasta}: {str(exc)[:120]}")
    return entregues


def _abrir_site(path: Path, log=None) -> tuple[Path | None, bool]:
    """O site chega como .zip: descompacta ao lado e abre o index.html.

    22/09/2026: pedido "cria um site" terminava com um .zip largado em
    JarvisResultados e a frase "o arquivo foi criado". Quem pediu um site quer
    VER o site. Devolve (pasta do site, abriu?).
    """
    import zipfile  # noqa: PLC0415
    try:
        if path.suffix.lower() in (".html", ".htm"):
            return path.parent, _abrir_arquivo(path, log=log)
        if path.suffix.lower() != ".zip":
            return None, False
        pasta = path.with_suffix("")
        n = 2
        while pasta.exists():
            pasta = path.parent / f"{path.stem}_{n}"
            n += 1
        raiz = pasta.resolve()
        with zipfile.ZipFile(path) as z:
            for membro in z.infolist():
                # Nada sai da pasta do site (caminho absoluto ou "..").
                destino = (pasta / membro.filename).resolve()
                if destino != raiz and raiz not in destino.parents:
                    continue
                z.extract(membro, pasta)
        indices = sorted(pasta.rglob("index.html"), key=lambda p: len(p.parts))
        if not indices:
            return pasta, False
        return pasta, _abrir_arquivo(indices[0], log=log)
    except Exception as exc:
        if log:
            log(f"[JARVIS] Nao consegui abrir o site: {str(exc)[:120]}")
        return None, False


def _abrir_arquivo(path: Path, log=None) -> bool:
    """Abre o arquivo no programa padrao do Windows (Bloco de Notas para .txt)."""
    try:
        if os.name == "nt":
            os.startfile(str(path))  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
        return True
    except Exception as exc:
        if log:
            log(f"[JARVIS] Nao consegui abrir o arquivo: {str(exc)[:120]}")
        return False


def remote_codex_job(parameters: dict, player=None, speak=None, prazo: float = 0.0) -> str:
    params = parameters or {}
    kind = str(params.get("kind") or "site").strip().lower()
    if kind not in {"site", "image", "leads", "image_edit", "chat"}:
        return "JARVIS só pode gerar um site, uma imagem ou uma lista de leads nesta ponte."
    description = str(params.get("description") or "").strip()
    if not description:
        if kind == "leads":
            return "Diga qual segmento e qual cidade você quer, por exemplo: 20 pizzarias com WhatsApp em João Pessoa."
        if kind == "image_edit":
            return "Diga o que devo mudar na imagem."
        return "Descreva o site ou a imagem que você quer criar."

    # Editar exige a foto em maos antes de qualquer coisa.
    imagem_bytes = None
    if kind == "image_edit":
        origem = _achar_imagem(params, player)
        if origem is None:
            return ("Não encontrei a imagem. Arraste a foto para a janela do JARVIS, "
                    "ou clique no quadro de arquivo para escolher, e peça de novo.")
        try:
            imagem_bytes = _preparar_imagem(origem)
        except Exception:
            return "Não consegui abrir essa imagem. Tente com outra foto."
    relay = _relay(player)
    if relay is None:
        return "Ainda não consigo iniciar essa criação agora. Tente novamente em alguns segundos."
    project_name = str(params.get("project_name") or "JarvisProjeto").strip() or "JarvisProjeto"

    def log(msg):
        if player is not None and hasattr(player, "write_log"):
            try:
                player.write_log(msg)
            except Exception:
                pass

    # Enquanto a tarefa roda, o painel mostra o que esta sendo feito. Antes
    # continuava escrito "OUVINDO" e o cliente nao sabia se algo acontecia.
    def estado(nome: str):
        if player is not None and hasattr(player, "set_state"):
            try:
                player.set_state(nome)
            except Exception:
                pass

    estados = {
        "leads": "SEARCHING_LEADS",
        "image_edit": "EDITING_IMAGE",
        "image": "CREATING_IMAGE",
        "site": "CREATING_SITE",
    }

    try:
        if kind == "leads":
            log("[JARVIS] Procurando as empresas no mapa...")
        elif kind == "image_edit":
            log("[JARVIS] Trabalhando na sua imagem...")
        else:
            log("[JARVIS] Preparando sua criação...")
        estado(estados.get(kind, "PROCESSING"))
        # Conversa nao espera 16 minutos: se o Codex nao respondeu em 3, a
        # resposta ja perdeu a utilidade numa conversa falada.
        #
        # E quem chama pode encurtar mais ainda. A tarefa por voz manda 30 s:
        # medido, ela leva de 4 a 9 s, entao 30 e folga de sobra — e se passar
        # disso o OpenRouter escreve o comando aqui mesmo, na hora, em vez de
        # deixar o dono ouvindo silencio.
        timeout = 1200 if kind == "leads" else (180 if kind == "chat" else 960)
        if prazo:
            timeout = min(timeout, float(prazo))
        result = relay.submit_codex_job(kind, description, project_name,
                                        timeout=timeout, imagem=imagem_bytes)
        path = Path(str(result.get("path") or ""))
        if not result.get("ok") or not path.exists():
            return "Não consegui confirmar o arquivo gerado."

        if kind == "chat":
            # Conversa: o que interessa e o TEXTO, nao o arquivo. O agente grava
            # em artifact/resposta.txt e a ponte devolve esse txt; aqui ele vira
            # a propria resposta do JARVIS.
            try:
                resposta = path.read_text(encoding="utf-8", errors="replace").strip()
            except Exception:
                return "O servidor respondeu, mas nao consegui ler o texto."
            try:
                path.unlink()          # nao deixa lixo na Area de Trabalho
            except Exception:
                pass
            return resposta or "O servidor respondeu vazio."

        if kind == "image_edit":
            # Abrir na hora poupa o cliente de ir procurar o arquivo.
            _abrir_arquivo(path, log=log)
            return f"Pronto. A imagem editada está em: {path}"

        if kind == "site":
            pasta_site, abriu = _abrir_site(path, log=log)
            if abriu:
                return ("Pronto, chefe. O site ficou pronto e já abri no navegador. "
                        f"Os arquivos estão em: {pasta_site}")
            return f"Concluído. O site foi criado na Área de Trabalho: {pasta_site or path}"

        if kind != "leads":
            return f"Concluído. O arquivo foi criado e verificado na Área de Trabalho: {path}"

        total = _contar_empresas(path)
        if total <= 0:
            return "A busca terminou, mas nenhuma empresa passou pelos filtros que você pediu."

        # O cliente manda o que fazer com o arquivo: onde guardar e se abre.
        destinos = params.get("destinos") or params.get("destino") or []
        if isinstance(destinos, str):
            destinos = [d for d in re.split(r",| e ", destinos) if d.strip()]
        copias = _entregar_copias(path, destinos, log=log)

        abrir = params.get("abrir")
        abrir = True if abrir is None else bool(abrir)
        alvo_abrir = copias[0] if copias else path
        aberto = _abrir_arquivo(alvo_abrir, log=log) if abrir else False

        empresa = "empresa" if total == 1 else "empresas"
        partes = [f"Pronto. Encontrei {total} {empresa}."]
        if copias:
            locais = ", ".join(str(c.parent) for c in dict.fromkeys(c.parent for c in copias))
            partes.append(f"Salvei em: {locais}.")
        else:
            partes.append(f"O arquivo está em: {path}")
        if aberto:
            partes.append("Já abri para você ver.")
        return " ".join(partes)

    except Exception as exc:
        message = str(exc).strip()[:400]
        # Do not expose implementation/provider names in customer-facing
        # speech or activity logs.
        message = re.sub(r"(?i)codex|openai", "JARVIS", message)
        return f"Não consegui concluir a criação: {message}"
    finally:
        # Termine como for, o painel volta a escutar o cliente.
        # "TAREFA_FIM" e nao "LISTENING": so este aviso solta o painel, para
        # que nenhum outro ponto do programa apague o "EDITANDO IMAGEM" antes
        # da hora.
        estado("TAREFA_FIM")
