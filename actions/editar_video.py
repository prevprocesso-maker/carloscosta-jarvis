# -*- coding: utf-8 -*-
"""EDITAR VIDEO de verdade, aqui no PC do cliente (10/09/2026).

Pedido do Diego: "edicao completa, o que o cliente pedir" — formato do TikTok
(9:16), espelhar, cortar trecho, cortar TODO silencio ("parou de falar, corta e
ja vai para a outra"), colocar imagem dentro do video, texto, efeito, musica,
legenda. E com uma exigencia: *"toma cuidado pra nao quebrar o JARVIS"*.

O DESENHO, EM TRES PECAS
------------------------
1. O PLANO. O cerebro (a mesma escada do desenho: nossa OpenAI -> Gemini ->
   Codex da VPS) recebe o pedido falado e a FICHA do video (duracao, tamanho,
   se tem som) e devolve uma receita em JSON — uma lista de passos de um
   CARDAPIO FECHADO. Sem cerebro, um leitor de palavras faz o basico (TikTok,
   silencio, espelhar, legenda...), entao a ferramenta nunca fica muda.
2. A RECEITA VIRA COMANDO DO FFMPEG, montado AQUI, por codigo. O cerebro nunca
   escreve linha de terminal. So o `filtro_livre` deixa ele mandar um filtro
   cru, e mesmo esse passa por uma lista de proibidos e cai fora se falhar.
3. O VIDEO NUNCA SAI DA MAQUINA. O que sobe para o cerebro e um texto de
   poucas linhas. O original nunca e tocado: o resultado nasce AO LADO dele,
   "nome (editado).mp4", e abre sozinho no fim.

O TRABALHO RODA EM SEGUNDO PLANO. A ferramenta devolve em segundos ("comecei,
vou fazer isto, leva uns N minutos") e o JARVIS continua conversando enquanto o
ffmpeg trabalha, com prioridade BAIXA para a voz nao engasgar. Quando termina,
ele avisa falando. Um trabalho por vez; da para perguntar o andamento e mandar
parar (`acao=status` / `acao=cancelar`).

AS PEDRAS QUE JA CUSTARAM CARO (edicao dos videos do Diego, 23/08 a 09/09):
- O corte de silencio e pelo VOLUME (envelope RMS), NUNCA pelos intervalos do
  whisper: o whisper fecha a palavra antes de o som acabar e corta fala.
- `select` com centenas de termos nao aguenta: os cortes sao trim+concat.
- O grafo de filtros vai por ARQUIVO (a linha de comando do Windows tem teto),
  e o ffmpeg 7+ trocou `-filter_complex_script` por `-/filter_complex`.
- O ffmpeg que vem no instalador e o 6.1 "essentials" do gyan.dev (tem x264,
  libass, freetype, NVENC). Ele mora em whatsapp_bridge/node_modules/
  ffmpeg-static — o mesmo que o WhatsApp ja usa para converter audio.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import unicodedata
from pathlib import Path

_RAIZ = Path(__file__).resolve().parents[1]

# Janela preta do console escondida, e o ffmpeg com prioridade ABAIXO do
# normal: renderizar come todos os nucleos, e sem isso a voz do JARVIS engasga
# enquanto o video sai.
_SEM_JANELA = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_PRIORIDADE_BAIXA = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)

EXT_VIDEO = {".mp4", ".mov", ".mkv", ".avi", ".wmv", ".webm", ".m4v", ".3gp",
             ".flv", ".mpg", ".mpeg", ".ts", ".mts", ".m2ts"}
EXT_IMAGEM = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
EXT_AUDIO = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".wma", ".opus"}


# ═════════════════════════════════════════════════════════════════════════════
#  O FFMPEG
# ═════════════════════════════════════════════════════════════════════════════

_FFMPEG: str | None = None
_FFMPEG_VERSAO = 0
_FILTROS: set | None = None
_CODIFICADOR: str | None = None


def _versao_do(ff: str) -> int:
    try:
        r = subprocess.run([ff, "-hide_banner", "-version"], capture_output=True,
                           timeout=20, creationflags=_SEM_JANELA)
        txt = r.stdout.decode("utf-8", "replace")
    except Exception:
        return 0
    m = re.search(r"ffmpeg version (?:n)?(\d+)\.", txt)
    if m:
        return int(m.group(1))
    # compilacao de git ("ffmpeg version N-112345-g...") e sempre das novas
    return 99 if "ffmpeg version" in txt else 0


def achar_ffmpeg() -> str | None:
    """O ffmpeg que vem com o JARVIS; o do sistema so se aquele faltar.

    O do instalador vem PRIMEIRO de proposito: e o unico cujas pecas eu
    conheco (libass para legenda, x264, NVENC). Um ffmpeg qualquer do PATH do
    cliente pode ser velho ou capado. O do Playwright nao entra: e uma
    compilacao minima, feita so para gravar tela.
    """
    global _FFMPEG, _FFMPEG_VERSAO
    if _FFMPEG and Path(_FFMPEG).is_file():
        return _FFMPEG
    candidatos = [
        _RAIZ / "tools" / "ffmpeg" / "ffmpeg.exe",
        _RAIZ / "whatsapp_bridge" / "node_modules" / "ffmpeg-static" / "ffmpeg.exe",
        _RAIZ / "whatsapp_bridge" / "node_modules" / "ffmpeg-static" / "ffmpeg",
    ]
    no_path = shutil.which("ffmpeg")
    if no_path:
        candidatos.append(Path(no_path))
    winget = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    if winget.is_dir():
        candidatos += sorted(winget.glob("Gyan.FFmpeg*/*/bin/ffmpeg.exe"), reverse=True)
    for c in candidatos:
        try:
            if c and Path(c).is_file():
                v = _versao_do(str(c))
                if v:
                    _FFMPEG, _FFMPEG_VERSAO = str(c), v
                    return _FFMPEG
        except Exception:
            continue
    return None


def garantir_ffmpeg_no_path() -> bool:
    """Poe o ffmpeg do JARVIS no PATH deste processo, se o PATH nao tiver um.

    Existe pelo `file_processor`: ele chama "ffmpeg" pelo nome, e no PC do
    cliente nao ha ffmpeg no PATH — o "cortar/comprimir video" que ja existia
    respondia "ffmpeg not found" desde sempre, com o programa DENTRO do
    instalador. Mudar o PATH do proprio processo vale para todo subprocesso
    aberto depois, sem tocar em uma linha do `file_processor`.
    """
    try:
        if shutil.which("ffmpeg"):
            return True
        ff = achar_ffmpeg()
        if not ff:
            return False
        pasta = str(Path(ff).parent)
        os.environ["PATH"] = pasta + os.pathsep + os.environ.get("PATH", "")
        return True
    except Exception:
        return False


def _filtros_disponiveis(ff: str) -> set:
    global _FILTROS
    if _FILTROS is None:
        try:
            r = subprocess.run([ff, "-hide_banner", "-filters"], capture_output=True,
                               timeout=30, creationflags=_SEM_JANELA)
            nomes = set()
            for linha in r.stdout.decode("utf-8", "replace").splitlines():
                partes = linha.split()
                if len(partes) >= 3 and re.fullmatch(r"[TSC.|]{2,3}", partes[0]):
                    nomes.add(partes[1])
            _FILTROS = nomes
        except Exception:
            _FILTROS = set()
    return _FILTROS


def _codificador(ff: str) -> str:
    """NVENC se a placa da NVIDIA aceitar; senao, o x264 do processador.

    O teste e um video preto de 0,2 s. AMD e Intel ficam de fora de proposito:
    nao tenho essas placas para conferir os parametros, e codificador que
    falha no meio do video e pior que codificador lento.
    """
    global _CODIFICADOR
    if _CODIFICADOR:
        return _CODIFICADOR
    _CODIFICADOR = "libx264"
    try:
        r = subprocess.run(
            [ff, "-hide_banner", "-v", "error", "-f", "lavfi", "-i",
             "color=c=black:s=320x240:d=0.2", "-c:v", "h264_nvenc", "-f", "null", "-"],
            capture_output=True, timeout=30, creationflags=_SEM_JANELA)
        if r.returncode == 0:
            _CODIFICADOR = "h264_nvenc"
    except Exception:
        pass
    return _CODIFICADOR


# ═════════════════════════════════════════════════════════════════════════════
#  A FICHA DO VIDEO
# ═════════════════════════════════════════════════════════════════════════════

def _segundos(txt) -> float | None:
    """'83.4', '1:23', '01:02:03.5', 83 -> segundos. None se nao der."""
    if txt is None or txt == "":
        return None
    if isinstance(txt, (int, float)):
        return float(txt)
    s = str(txt).strip().lower().replace(",", ".")
    s = re.sub(r"\s*(s|seg|segundos?)$", "", s)
    try:
        if ":" in s:
            partes = [float(p) for p in s.split(":")]
            total = 0.0
            for p in partes:
                total = total * 60 + p
            return total
        return float(s)
    except Exception:
        return None


def ficha_do_video(caminho: str) -> dict:
    """Duracao, tamanho NA TELA, fps e se tem som — lidos do proprio ffmpeg.

    O instalador nao traz o ffprobe, entao a ficha sai do texto que o
    `ffmpeg -i` imprime. Video de CELULAR vem gravado deitado com uma marca de
    "gire 90 graus"; o ffmpeg ja gira ao decodificar, entao a largura e a
    altura aqui sao as da TELA (trocadas quando a marca e de 90/270).
    """
    ff = achar_ffmpeg()
    ficha = {"ok": False, "caminho": str(caminho), "duracao": 0.0, "largura": 0,
             "altura": 0, "fps": 30.0, "tem_audio": False, "rotacao": 0}
    if not ff:
        ficha["erro"] = "sem ffmpeg"
        return ficha
    try:
        r = subprocess.run([ff, "-hide_banner", "-nostdin", "-i", str(caminho)],
                           capture_output=True, timeout=60, creationflags=_SEM_JANELA)
        txt = r.stderr.decode("utf-8", "replace")
    except Exception as exc:
        ficha["erro"] = str(exc)[:120]
        return ficha
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", txt)
    if m:
        ficha["duracao"] = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    mv = re.search(r"Stream #\d+:\d+[^\n]*?: Video: ([^\n]+)", txt)
    if not mv:
        ficha["erro"] = "o arquivo nao tem imagem de video"
        return ficha
    linha = mv.group(1)
    mt = re.search(r"\b(\d{2,5})x(\d{2,5})\b", linha)
    if mt:
        ficha["largura"], ficha["altura"] = int(mt.group(1)), int(mt.group(2))
    mf = re.search(r"([\d.]+) fps", linha) or re.search(r"([\d.]+) tbr", linha)
    if mf:
        try:
            ficha["fps"] = float(mf.group(1))
        except Exception:
            pass
    mr = (re.search(r"rotation of (-?[\d.]+) degrees", txt)
          or re.search(r"rotate\s*:\s*(-?\d+)", txt))
    if mr:
        try:
            ficha["rotacao"] = int(round(float(mr.group(1)))) % 360
        except Exception:
            pass
    if ficha["rotacao"] in (90, 270):
        ficha["largura"], ficha["altura"] = ficha["altura"], ficha["largura"]
    ficha["tem_audio"] = bool(re.search(r"Stream #\d+:\d+[^\n]*?: Audio:", txt))
    ficha["codec"] = linha.split()[0].strip(",") if linha.split() else ""
    ficha["ok"] = ficha["duracao"] > 0.1 and ficha["largura"] > 0 and ficha["altura"] > 0
    if not ficha["ok"] and "erro" not in ficha:
        ficha["erro"] = "nao consegui ler a duracao ou o tamanho"
    return ficha


def _fps_de_saida(fps: float) -> str:
    """O fps do video pronto, como texto para o filtro `fps`.

    Celular grava em fps VARIAVEL (29,84; 30,02...). Normalizar para um valor
    fixo logo na entrada e o que deixa os cortes cairem no quadro certo.
    """
    if 29.9 < fps < 29.99:
        return "30000/1001"
    if 59.9 < fps < 59.99:
        return "60000/1001"
    f = int(round(fps or 30))
    return str(max(15, min(60, f)))


def _par(n: float) -> int:
    n = int(round(n))
    return max(2, n - (n % 2))


# ═════════════════════════════════════════════════════════════════════════════
#  ACHAR OS ARQUIVOS (o video, a imagem, a musica)
# ═════════════════════════════════════════════════════════════════════════════

def _sem_acento(texto: str) -> str:
    s = unicodedata.normalize("NFKD", str(texto or "").lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def _palavras(texto: str) -> list:
    s = _sem_acento(texto)
    return "".join(c if c.isalnum() else " " for c in s).split()


# Palavras que so dizem O TIPO do arquivo, nao QUAL arquivo. "A minha foto"
# nao ajuda a achar nada; "a foto da praia" ajuda pelo "praia".
_GENERICAS = set("""
o a os as um uma de do da dos das meu minha meus minhas seu sua esse essa este
esta isso aquele aquela no na nos nas em com pra para por que ta aqui ali ai
arquivo arquivos imagem imagens foto fotos figura video videos filme musica
musicas audio som trilha pasta mesma mesmo area trabalho downloads download
documentos imagens videos ultima ultimo novo nova
""".split())

_PULAR_PASTAS = {"node_modules", "appdata", "$recycle.bin", "windows", "program files",
                 ".git", "__pycache__", "cache", "temp", "tmp"}


def _pastas_do_dono() -> list:
    casa = Path.home()
    nomes = ["Desktop", "Área de Trabalho", "Downloads", "Videos", "Vídeos",
             "Pictures", "Imagens", "Music", "Músicas", "Documents", "Documentos"]
    pastas = []
    for base in (casa, casa / "OneDrive"):
        for n in nomes:
            p = base / n
            try:
                if p.is_dir() and p not in pastas:
                    pastas.append(p)
            except Exception:
                continue
    return pastas


def _listar(pasta: Path, extensoes: set, profundidade: int = 2, teto: int = 4000):
    """Arquivos com as extensoes pedidas, descendo poucas pastas e com teto."""
    achados, fila, vistos = [], [(pasta, 0)], 0
    while fila and vistos < teto:
        atual, nivel = fila.pop(0)
        try:
            itens = list(os.scandir(atual))
        except Exception:
            continue
        for it in itens:
            vistos += 1
            try:
                if it.is_dir(follow_symlinks=False):
                    if nivel < profundidade and it.name.lower() not in _PULAR_PASTAS \
                            and not it.name.startswith("."):
                        fila.append((Path(it.path), nivel + 1))
                elif Path(it.name).suffix.lower() in extensoes:
                    achados.append(Path(it.path))
            except Exception:
                continue
    return achados


def achar_arquivo(nome, extensoes: set, perto_de: Path | None = None) -> str:
    """Caminho do arquivo que o dono citou, ou "" se nao achar com seguranca.

    Aceita caminho inteiro, so o nome ("logo.png") ou pedaco dele ("a logo da
    oficina"). Procura primeiro na pasta do video, depois nas pastas do dono.
    Varios candidatos: fica o mais recente — e o que a pessoa acabou de baixar.
    """
    bruto = str(nome or "").strip().strip('"').strip("'")
    if not bruto:
        return ""
    try:
        p = Path(os.path.expandvars(os.path.expanduser(bruto)))
        if p.is_file() and p.suffix.lower() in extensoes:
            return str(p)
    except Exception:
        p = Path(bruto)
    base = p.stem if p.suffix.lower() in extensoes else bruto
    todas = _palavras(base)
    chaves = [w for w in todas if w not in _GENERICAS and len(w) > 1]
    pastas = ([perto_de] if perto_de else []) + _pastas_do_dono()
    if not chaves:
        # So palavra generica ("a imagem", "a musica que esta na pasta"): vale
        # se na pasta do video existir UM arquivo daquele tipo, e so um.
        if perto_de:
            unicos = _listar(perto_de, extensoes, profundidade=0)
            if len(unicos) == 1:
                return str(unicos[0])
        return ""
    candidatos = []
    for pasta in pastas:
        for arq in _listar(pasta, extensoes, profundidade=(1 if pasta == perto_de else 2)):
            nome_arq = " ".join(_palavras(arq.stem))
            if all(w in nome_arq for w in chaves):
                candidatos.append(arq)
        if candidatos and pasta == perto_de:
            break
    if not candidatos:
        return ""
    candidatos.sort(key=lambda a: a.stat().st_mtime, reverse=True)
    return str(candidatos[0])


def _video_selecionado_no_explorer() -> str:
    """O video SELECIONADO na janela do Explorer (a da frente ganha)."""
    try:
        import pythoncom         # noqa: PLC0415
        import win32com.client   # noqa: PLC0415
        import win32gui          # noqa: PLC0415
    except Exception:
        return ""
    try:
        pythoncom.CoInitialize()
    except Exception:
        pass
    achados = []
    try:
        frente = win32gui.GetForegroundWindow()
    except Exception:
        frente = 0
    try:
        shell = win32com.client.Dispatch("Shell.Application")
        for janela in shell.Windows():
            try:
                itens = janela.Document.SelectedItems()
                for i in range(itens.Count):
                    caminho = str(itens.Item(i).Path)
                    if Path(caminho).suffix.lower() in EXT_VIDEO and Path(caminho).is_file():
                        achados.append((int(janela.HWND) == int(frente), caminho))
            except Exception:
                continue
    except Exception:
        return ""
    for na_frente, caminho in achados:
        if na_frente:
            return caminho
    return achados[-1][1] if achados else ""


def _video_do_pedido(p: dict, player) -> str:
    """De onde vem o video: nome/caminho dito, o arrastado para a janela do
    JARVIS, ou o selecionado no Explorer. Nunca "o ultimo da pasta": editar o
    video errado faria o dono esperar minutos por nada."""
    atual = ""
    try:
        cf = getattr(player, "current_file", None) if player is not None else None
        if cf and Path(str(cf)).is_file() and Path(str(cf)).suffix.lower() in EXT_VIDEO:
            atual = str(cf)
    except Exception:
        pass
    for chave in ("video", "arquivo", "file_path", "caminho"):
        dito = str(p.get(chave) or "").strip()
        if not dito:
            continue
        # O cerebro costuma repetir o nome do video ARRASTADO. Se o que ele
        # disse bate com esse arquivo, e ele — sem sair procurando no disco,
        # onde um "teste 2.mp4" mais novo roubaria o lugar do "teste.mp4".
        if atual:
            chaves = [w for w in _palavras(Path(dito).stem) if w not in _GENERICAS]
            if not chaves or all(w in " ".join(_palavras(Path(atual).stem)) for w in chaves):
                return atual
        achado = achar_arquivo(dito, EXT_VIDEO)
        if achado:
            return achado
    if atual:
        return atual
    # O video que o dono arrastou ANTES das imagens (a janela so guarda um
    # arquivo; quem lembra dele e o main.py).
    lembrado = str(p.get("_video_lembrado") or "").strip()
    if lembrado and Path(lembrado).is_file() and Path(lembrado).suffix.lower() in EXT_VIDEO:
        return lembrado
    return _video_selecionado_no_explorer()


# ═════════════════════════════════════════════════════════════════════════════
#  O PLANO: pedido falado -> lista de passos do cardapio
# ═════════════════════════════════════════════════════════════════════════════

_INSTRUCAO_PLANO = """Voce e o editor de video do JARVIS. O cliente pediu uma edicao e voce devolve SO um JSON, sem nenhum texto antes ou depois, neste formato:
{"passos": [ ... ], "nao_da": "..."}

nao_da: o que ele pediu e NAO existe no cardapio abaixo, em poucas palavras (vazio se tudo couber).
passos: SO o que ele pediu, usando esta lista fechada:

{"tipo":"formato","proporcao":"9:16"|"16:9"|"1:1"|"4:5","modo":"desfocado"|"preencher"|"barras","foco":"centro"|"esquerda"|"direita"}
  9:16 = TikTok, Reels, Shorts, Status, Kwai, vertical, em pe, tela do celular. 16:9 = YouTube, horizontal, deitado. 1:1 = quadrado. 4:5 = feed do Instagram.
  modo desfocado (padrao) = o video inteiro no meio e ele mesmo desfocado atras; preencher = enche a tela cortando as sobras (quando ele disser tela cheia, sem borda, cortar as laterais, dar zoom); barras = tarja preta.
{"tipo":"cortar_silencio","silencio_minimo":0.5}
  tira as partes em que ninguem fala. silencio_minimo em segundos: "todo silencio", "bem dinamico", "sem respiro" = 0.3; normal = 0.5; "so as pausas grandes" = 1.0. Numero dito por ele vale ("pausa de mais de 2 segundos" = 2; "mais de meio segundo sem falar" = 0.5).
{"tipo":"manter_trecho","inicio":S,"fim":S}   fica SO esse pedaco (segundos do video ORIGINAL).
{"tipo":"remover_trecho","inicio":S,"fim":S}  tira esse pedaco (segundos do ORIGINAL). Pode repetir.
{"tipo":"espelhar"}  inverte esquerda e direita.   {"tipo":"virar"}  de cabeca para baixo.   {"tipo":"girar","graus":90|-90|180}  (90 = horario)
{"tipo":"velocidade","fator":F}   0.25 a 4 (acelerar 2x = 2; camera lenta = 0.5).
{"tipo":"texto","texto":"...","posicao":"topo"|"centro"|"baixo","inicio":S,"fim":S,"tamanho":"pequeno"|"medio"|"grande","cor":"branco"|"amarelo"|"vermelho"|"verde"|"azul"|"preto"|"rosa"|"laranja","fundo":true|false}
{"tipo":"imagem","arquivo":"nome ou caminho que ele disse","posicao":"canto_superior_direito"|"canto_superior_esquerdo"|"canto_inferior_direito"|"canto_inferior_esquerdo"|"centro"|"topo"|"baixo"|"faixa_de_cima"|"faixa_de_baixo"|"tela_cheia","tamanho":25,"inicio":S,"fim":S,"opacidade":1}
  tamanho = % da largura do video: pequeno 12, logo/marca d'agua 20, grande 35, "bem grande" 50, metade da tela 50. Sem posicao dita: canto_superior_direito.
  faixa_de_cima / faixa_de_baixo = video DEITADO virando vertical (TikTok): o video fica no meio e a imagem ocupa a faixa inteira acima ou abaixo dele (o tamanho se ajusta sozinho). E o que ele quer com "uma imagem em cima e outra embaixo", "a de cima", "a de baixo", "em cima do video", "embaixo do video".
{"tipo":"salvar","onde":"area_de_trabalho"|"downloads"|"videos"|"documentos"}   onde o video pronto fica, SO se ele disser ("salva na area de trabalho", "coloca no desktop"). Sem isso: nao mande o passo (fica ao lado do original).
{"tipo":"legenda","estilo":"tiktok"|"simples","idioma":"pt"|"en"|"es"}
  legenda automatica do que e falado. tiktok = poucas palavras grandes com a palavra da vez em amarelo (padrao); simples = frase inteira embaixo.
{"tipo":"musica","arquivo":"nome ou caminho","volume":0.15}   musica de fundo; ela abaixa sozinha quando alguem fala. Video sem voz: volume 0.6.
{"tipo":"som","acao":"mudo"|"aumentar"|"diminuir"|"normalizar","fator":1.5}
{"tipo":"efeito","nome":"preto_e_branco"|"sepia"|"vintage"|"cores_vivas"|"desbotado"|"clarear"|"escurecer"|"contraste"|"nitidez"|"desfoque"|"vinheta"|"quente"|"frio"|"negativo"|"cinema"|"tremer"|"zoom_lento"|"zoom_nos_cortes","intensidade":1}
  zoom_nos_cortes = aproxima e afasta a cada corte, estilo youtuber (combina com cortar_silencio). tremer = camera tremendo. cinema = faixas pretas em cima e embaixo. intensidade de 0.5 a 2.
{"tipo":"transicao","entrada":1,"saida":1}   escurece no comeco e no fim (segundos).
{"tipo":"juntar","arquivos":["nome ou caminho"],"posicao":"depois"|"antes"}   emenda outros videos neste.
{"tipo":"ao_contrario"}   video de tras para frente (so video de ate 15 s).
{"tipo":"qualidade","nivel":"leve"|"normal"|"alta"}   leve = arquivo pequeno para mandar no WhatsApp. alta = "Full HD", "1080p", "HD", "qualidade maxima": o video JA SAI em Full HD (1080x1920 no vertical, 1920x1080 no horizontal) — Full HD, HD e 1080p NUNCA vao em nao_da.
{"tipo":"filtro_livre","video":"filtro do ffmpeg"}   SO se nenhum item acima servir para o efeito pedido: um filtro simples de uma linha, sem colchetes e sem ponto e virgula.

TEMPO: numeros em segundos. Em texto, imagem e transicao o tempo e do video FINAL; negativo conta do fim (inicio -5 = ultimos 5 s); sem inicio e fim = o video todo.
REGRAS:
- Nao invente passo que ele nao pediu, e nunca invente nome de arquivo: use o que ele falou.
- IMAGENS QUE O CLIENTE MANDOU: quando vier essa lista, "essa imagem", "essas imagens", "as duas imagens", "a logo", "a foto" sao elas: use o caminho EXATO no "arquivo", um passo de imagem para cada uma que ele pediu. Se ele disser qual vai onde pelo que tem nela ("a do site em cima", "a do robo embaixo"), case pela descricao de cada uma.
- "Inverter" sozinho = espelhar; junto com vertical/horizontal/em pe/deitado = formato.
- "Cortar" sem dizer onde nem o que = cortar_silencio. "Deixar so a parte falando" = cortar_silencio.
- "Edicao completa", "edita do seu jeito", "deixa profissional": cortar_silencio 0.4, legenda tiktok, efeito zoom_nos_cortes e som normalizar (mais formato 9:16 se ele citar rede social vertical).
- Se vier uma EDICAO ANTERIOR e ele estiver acrescentando ("agora", "tambem", "e ainda", "coloca mais"), devolva a lista COMPLETA: os passos anteriores MAIS os novos. Se ele pedir de outro jeito ("nao", "refaz", "so"), devolva so o que vale agora."""


def _descrever_ficha(ficha: dict, nome: str) -> str:
    w, h = ficha.get("largura", 0), ficha.get("altura", 0)
    orient = "vertical" if h > w * 1.05 else "horizontal" if w > h * 1.05 else "quadrado"
    d = ficha.get("duracao", 0.0)
    return (f"VIDEO: {nome} | {d:.1f} s ({int(d // 60)}min{int(d % 60):02d}s) | {w}x{h} {orient} | "
            f"{ficha.get('fps', 30):.0f} fps | {'com som' if ficha.get('tem_audio') else 'SEM som'}")


def _so_json(texto: str) -> dict:
    s = str(texto or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", s, re.S)
    if m:
        s = m.group(1).strip()
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        return {}
    try:
        dados = json.loads(s[i:j + 1])
    except Exception:
        try:
            dados = json.loads(re.sub(r",\s*([}\]])", r"\1", s[i:j + 1]))
        except Exception:
            return {}
    return dados if isinstance(dados, dict) else {}


def _negado(t: str, pos: int) -> bool:
    """A palavra na posicao `pos` vem logo depois de um "nao"/"sem"?"""
    antes = t[max(0, pos - 22):pos]
    return bool(re.search(r"\b(nao|sem|tira[r]? a|nada de)\b[^.,;]*$", antes))


def _tem(t: str, padrao: str) -> bool:
    for m in re.finditer(padrao, t):
        if not _negado(t, m.start()):
            return True
    return False


def _plano_por_palavras(pedido: str) -> list:
    """O basico sem cerebro nenhum: palavra-chave -> passo.

    Serve de rede em dois momentos: quando o cerebro esta fora (o JARVIS nunca
    fica mudo por isso) e para conferir o que o cerebro devolveu nos pedidos
    mais comuns (formato, silencio, legenda), que nao podem sumir.
    """
    t = " ".join(_palavras(pedido))
    passos = []
    modo = ("preencher" if re.search(r"tela cheia|sem borda|sem tarja|preench|laterais|enche", t)
            else "barras" if re.search(r"tarja|barra preta|faixa preta|bordas? pretas?", t)
            else "desfocado")
    if _tem(t, r"\btik ?tok|\breels?\b|\bshorts?\b|\bkwai\b|\bstor(y|ies)\b|\bstatus\b|"
               r"\bvertical\b|\bem pe\b|\b9 ?(x|por)? ?16\b"):
        passos.append({"tipo": "formato", "proporcao": "9:16", "modo": modo})
    elif _tem(t, r"\bquadrad|\b1 ?(x|por) ?1\b"):
        passos.append({"tipo": "formato", "proporcao": "1:1", "modo": modo})
    elif _tem(t, r"\bhorizontal\b|\bdeitad|\byoutube\b|\b16 ?(x|por)? ?9\b"):
        passos.append({"tipo": "formato", "proporcao": "16:9", "modo": modo})
    if _tem(t, r"\bsilencio|\bpausa|so (a|as) (parte|partes) (falad|que fal)|so falando|\brespiro|"
               r"sem falar|nao (estou|esteja|to|tou|estiver|estava) falando|parei de falar|parado calado"):
        minimo = 0.3 if re.search(r"\btodo|\btodos|\bqualquer|\bdinamic", t) else 0.5
        # O numero que ele disse vale: "mais de meio segundo sem falar",
        # "pausa de mais de 2 segundos". So o numero PERTO do silencio — "corta
        # os primeiros 5 segundos" e outra coisa.
        perto = " ".join(t[max(0, m.start() - 45):m.end() + 45] for m in re.finditer(
            r"silencio|pausa|sem falar|falando|calado|respiro", t))
        if re.search(r"\bmeio segundo", perto):
            minimo = 0.5
        else:
            m = re.search(r"\b(?:mais de|acima de|de|por)\s+(\d+(?:[.,]\d+)?|um|uma|dois|duas|tres)"
                          r" segundos?\b", perto)
            if m:
                n = {"um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3}.get(m.group(1))
                minimo = float(n if n else m.group(1).replace(",", "."))
        passos.append({"tipo": "cortar_silencio", "silencio_minimo": minimo})
    if _tem(t, r"\bespelh") or (re.search(r"\binvert", t) and not re.search(
            r"vertical|horizontal|em pe|deitad|cabeca", t)):
        passos.append({"tipo": "espelhar"})
    if re.search(r"cabeca (pra|para) baixo", t):
        passos.append({"tipo": "virar"})
    if _tem(t, r"\blegenda"):
        passos.append({"tipo": "legenda",
                       "estilo": "simples" if "simples" in t else "tiktok",
                       "idioma": "en" if re.search(r"\bingles\b|\benglish\b", t) else "pt"})
    if re.search(r"camera lenta|camara lenta|\bdevagar|mais lento|slow", t):
        passos.append({"tipo": "velocidade", "fator": 0.5})
    elif re.search(r"\bacelera|mais rapido|\b2 ?x\b|dobro da velocidade", t):
        passos.append({"tipo": "velocidade", "fator": 2.0 if re.search(r"\b2 ?x\b|dobro", t) else 1.5})
    for padrao, nome in ((r"preto e branco|\bp ?b\b", "preto_e_branco"), (r"\bsepia", "sepia"),
                         (r"vintage|\bretro\b|\bantigo\b", "vintage"),
                         (r"cores vivas|mais cor|\bsatura", "cores_vivas"),
                         (r"\bvinheta", "vinheta"), (r"\bnegativo\b", "negativo"),
                         (r"\bcinema\b|cinematic", "cinema"), (r"\btrem(e|er|ida|endo)\b", "tremer")):
        if _tem(t, padrao):
            passos.append({"tipo": "efeito", "nome": nome, "intensidade": 1})
    if re.search(r"\bzoom\b", t):
        passos.append({"tipo": "efeito",
                       "nome": "zoom_nos_cortes" if "corte" in t else "zoom_lento",
                       "intensidade": 1})
    if re.search(r"sem som|tira o som|tirar o som|\bmudo\b|sem audio|sem o audio", t):
        passos.append({"tipo": "som", "acao": "mudo"})
    elif re.search(r"aumenta o (som|volume|audio)|mais alto", t):
        passos.append({"tipo": "som", "acao": "aumentar", "fator": 1.6})
    elif re.search(r"\bnormaliz|equilibr", t):
        passos.append({"tipo": "som", "acao": "normalizar"})
    if re.search(r"\bfade\b|\btransica|escurec(e|er) no (comeco|fim|final)", t):
        passos.append({"tipo": "transicao", "entrada": 1, "saida": 1})
    if re.search(r"whatsapp|\bleve\b|comprim|diminu(i|ir) o tamanho|mais leve", t):
        passos.append({"tipo": "qualidade", "nivel": "leve"})
    if re.search(r"edicao completa|do seu jeito|profissional|deixa bonito|capricha", t) and not passos:
        passos += [{"tipo": "cortar_silencio", "silencio_minimo": 0.4},
                   {"tipo": "legenda", "estilo": "tiktok", "idioma": "pt"},
                   {"tipo": "efeito", "nome": "zoom_nos_cortes", "intensidade": 1},
                   {"tipo": "som", "acao": "normalizar"}]
    onde = _onde_salvar(t)
    if onde:
        passos.append({"tipo": "salvar", "onde": onde})
    return passos


def _onde_salvar(t: str) -> str:
    """"salva na area de trabalho" -> 'area_de_trabalho'. Texto ja sem acento."""
    if re.search(r"\barea de trabalho\b|\bdesktop\b|\btela inicial\b", t):
        return "area_de_trabalho"
    if re.search(r"\b(na|pra|para a|para|a) (pasta (de |dos? )?)?downloads?\b", t):
        return "downloads"
    if re.search(r"\b(na|pra|para a|para) pasta (de |dos? )?videos\b|\bmeus videos\b", t):
        return "videos"
    if re.search(r"\b(na|pra|para a|para) pasta (de |dos? )?documentos\b|\bmeus documentos\b", t):
        return "documentos"
    return ""


def _forma_da_imagem(ff: str, caminho: str) -> str:
    """"deitada (1881x836)" — o cerebro precisa saber se e faixa ou logo."""
    try:
        r = subprocess.run([ff, "-hide_banner", "-i", caminho], capture_output=True, text=True,
                           errors="replace", timeout=15, creationflags=_SEM_JANELA)
        m = re.search(r"Stream #.*?Video:.*?\b(\d{2,5})x(\d{2,5})\b", r.stderr or "")
        if not m:
            return ""
        w, h = int(m.group(1)), int(m.group(2))
        forma = "deitada" if w > h * 1.15 else "em pe" if h > w * 1.15 else "quadrada"
        return f"{forma} ({w}x{h})"
    except Exception:
        return ""


_PERGUNTA_DA_IMAGEM = ("Em uma linha de ate 20 palavras, em portugues: qual o TITULO principal (o "
                       "texto maior) desta imagem e do que ela fala? Sem texto nela, diga o que aparece.")


def _descrever_uma(ff: str, caminho: str, ver, log) -> str:
    """O que tem na imagem, numa linha. "" quando ninguem conseguiu olhar.

    Quem olha: a nossa OpenAI (o mesmo modelo e esforco do cerebro, ~2 s),
    e se ela falhar o Codex da VPS (`ver`, o olho do desenho). A foto vai
    reduzida para 768 px: o titulo continua legivel e o envio e de ~70 KB.
    """
    try:
        jpg = subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-i", caminho,
                              "-vf", "scale=768:-2", "-frames:v", "1", "-q:v", "5",
                              "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"],
                             capture_output=True, timeout=20, creationflags=_SEM_JANELA).stdout
    except Exception:
        jpg = b""
    if not jpg:
        return ""
    try:
        import base64  # noqa: PLC0415
        from core.cerebro_openai import ESFORCO, MODELO, CerebroOpenAI, _postar  # noqa: PLC0415
        c = CerebroOpenAI()
        corpo = {"model": MODELO, "reasoning": {"effort": ESFORCO}, "max_output_tokens": 160,
                 "input": [{"role": "user", "content": [
                     {"type": "input_text", "text": _PERGUNTA_DA_IMAGEM},
                     {"type": "input_image", "detail": "low",
                      "image_url": "data:image/jpeg;base64," + base64.b64encode(jpg).decode()}]}]}
        t = CerebroOpenAI._texto_da_saida(_postar(c.chave, corpo, 25).get("output") or [])
        if t.strip():
            return re.sub(r"\s+", " ", t).strip()[:220]
    except Exception as exc:
        log(f"[editar_video] a OpenAI nao olhou a imagem: {str(exc)[:100]}")
    # A GEMINI DO CLIENTE ANTES DO CODEX (01/10/2026): a OpenAI do servidor
    # esta sem credito, e o Codex leva ~8 s por imagem; o flash-lite, ~1,7 s.
    try:
        from core.cerebro_rapido import olhar as _olhar  # noqa: PLC0415
        t = _olhar(jpg, _PERGUNTA_DA_IMAGEM)
        if t.strip():
            return re.sub(r"\s+", " ", t).strip()[:220]
    except Exception as exc:
        log(f"[editar_video] a Gemini nao olhou a imagem: {str(exc)[:100]}")
    if ver is not None:
        try:
            t = str(ver(jpg, _PERGUNTA_DA_IMAGEM) or "")
            if t.strip():
                return re.sub(r"\s+", " ", t).strip()[:220]
        except Exception as exc:
            log(f"[editar_video] o Codex nao olhou a imagem: {str(exc)[:100]}")
    return ""


def _descrever_imagens(ff: str, imagens: list, ver, log) -> list:
    """Olha todas ao mesmo tempo (duas imagens = ~2 s, nao 4)."""
    saida = [""] * len(imagens)

    def _uma(i, c):
        saida[i] = _descrever_uma(ff, c, ver, log)
    fios = [threading.Thread(target=_uma, args=(i, c), daemon=True) for i, c in enumerate(imagens)]
    for f in fios:
        f.start()
    limite = time.monotonic() + 30
    for f in fios:
        f.join(max(0.1, limite - time.monotonic()))
    return saida


def _planejar(pedido: str, ficha: dict, nome: str, gerar, anterior: dict | None,
              log, imagens=None) -> tuple:
    """(passos brutos, nao_da, de onde veio). Nunca levanta excecao.

    `imagens`: as que o dono arrastou, [{"arquivo", "descricao", "forma"}] na
    ordem em que chegaram (um caminho solto tambem vale).
    """
    texto = [_descrever_ficha(ficha, nome)]
    if isinstance(imagens, str):
        imagens = [{"arquivo": imagens}] if imagens else []
    if imagens:
        linhas = ["IMAGENS QUE O CLIENTE MANDOU AGORA (na ordem em que arrastou):"]
        for i, im in enumerate(imagens, 1):
            extra = ""
            if im.get("forma"):
                extra += f" | imagem {im['forma']}"
            if im.get("descricao"):
                extra += f" | o que tem nela: {im['descricao']}"
            linhas.append(f"{i}) \"arquivo\": \"{im['arquivo']}\"{extra}")
        texto.append("\n".join(linhas))
    if anterior and anterior.get("passos"):
        texto.append("EDICAO ANTERIOR DESTE VIDEO (feita ha pouco): "
                     + json.dumps(anterior["passos"], ensure_ascii=False))
    texto.append("PEDIDO DO CLIENTE: " + pedido)
    passos, nao_da, fonte = [], "", "palavras"
    if gerar is not None:
        try:
            bruto = gerar("\n".join(texto), _INSTRUCAO_PLANO)
            dados = _so_json(bruto)
            if isinstance(dados.get("passos"), list):
                passos = [x for x in dados["passos"] if isinstance(x, dict)]
                nao_da = str(dados.get("nao_da") or "").strip()
                fonte = "cerebro"
            else:
                log(f"[editar_video] o cerebro nao devolveu plano: {str(bruto)[:160]!r}")
        except Exception as exc:
            log(f"[editar_video] cerebro falhou no plano: {type(exc).__name__}: {str(exc)[:120]}")
    rede = _plano_por_palavras(pedido)
    if fonte == "palavras":
        base = list(anterior.get("passos") or []) if anterior and re.search(
            r"\b(agora|tambem|ainda|mais|alem)\b", " ".join(_palavras(pedido))) else []
        return base + rede, nao_da, fonte
    # Conferencia: os tres pedidos mais comuns nao podem sumir no caminho.
    tipos = {x.get("tipo") for x in passos}
    for extra in rede:
        if extra["tipo"] in ("formato", "cortar_silencio", "legenda", "salvar") and extra["tipo"] not in tipos:
            log(f"[editar_video] o cerebro esqueceu '{extra['tipo']}'; acrescentei")
            passos.append(extra)
    return passos, nao_da, fonte


# ── LIMPEZA DO PLANO ─────────────────────────────────────────────────────────

_PROPORCOES = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080),
               "4:5": (1080, 1350)}
_PROPORCOES_LEVE = {"9:16": (720, 1280), "16:9": (1280, 720), "1:1": (720, 720),
                    "4:5": (720, 900)}
_POSICOES_IMG = {"canto_superior_direito", "canto_superior_esquerdo",
                 "canto_inferior_direito", "canto_inferior_esquerdo", "centro", "topo",
                 "baixo", "tela_cheia", "faixa_de_cima", "faixa_de_baixo"}
_SINONIMOS_POSICAO = {"faixa_superior": "faixa_de_cima", "faixa_cima": "faixa_de_cima",
                      "acima_do_video": "faixa_de_cima", "em_cima_do_video": "faixa_de_cima",
                      "faixa_inferior": "faixa_de_baixo", "faixa_baixo": "faixa_de_baixo",
                      "abaixo_do_video": "faixa_de_baixo", "embaixo_do_video": "faixa_de_baixo",
                      "em_cima": "topo", "cima": "topo", "superior": "topo", "em_baixo": "baixo",
                      "embaixo": "baixo", "inferior": "baixo", "meio": "centro"}
_ONDE_SALVAR = {"area_de_trabalho", "downloads", "videos", "documentos"}
_CORES = {"branco": "FFFFFF", "amarelo": "FFE000", "vermelho": "FF2A2A",
          "verde": "22DD55", "azul": "2E8BFF", "preto": "000000", "rosa": "FF4FA8",
          "laranja": "FF8C1A", "roxo": "9B4DFF", "cinza": "BBBBBB"}
_EFEITOS = {"preto_e_branco", "sepia", "vintage", "cores_vivas", "desbotado", "clarear",
            "escurecer", "contraste", "nitidez", "desfoque", "vinheta", "quente", "frio",
            "negativo", "cinema", "tremer", "zoom_lento", "zoom_nos_cortes"}
_SINONIMOS_EFEITO = {"pb": "preto_e_branco", "preto e branco": "preto_e_branco",
                     "retro": "vintage", "antigo": "vintage", "saturado": "cores_vivas",
                     "brilho": "clarear", "mais claro": "clarear", "mais escuro": "escurecer",
                     "blur": "desfoque", "desfocar": "desfoque", "zoom": "zoom_lento",
                     "zoom_cortes": "zoom_nos_cortes", "jump_cut": "zoom_nos_cortes",
                     "treme": "tremer", "tremida": "tremer", "tremor": "tremer",
                     "shake": "tremer", "terremoto": "tremer"}
_PROIBIDO_NO_FILTRO_LIVRE = re.compile(
    r"movie|sendcmd|zmq|ladspa|lv2|frei0r|subtitles|\bass\b|drawtext|textfile|"
    r"filename|\bfile\b|\bf=|lut3d|haldclut|opencl|vulkan|cuda|[;\[\]\n\r'\"]", re.I)


def _num(v, padrao, minimo=None, maximo=None):
    try:
        x = float(v)
    except Exception:
        x = _segundos(v)
        if x is None:
            x = padrao
    if x is None:
        return None
    if minimo is not None:
        x = max(minimo, x)
    if maximo is not None:
        x = min(maximo, x)
    return x


def _proporcao(v) -> str:
    s = _sem_acento(v).replace(" ", "").replace("x", ":").replace("/", ":").replace("por", ":")
    if s in _PROPORCOES:
        return s
    if any(k in s for k in ("tiktok", "reels", "short", "vertical", "story", "celular")):
        return "9:16"
    if any(k in s for k in ("youtube", "horizontal", "deitado")):
        return "16:9"
    if "quadrad" in s:
        return "1:1"
    return ""


def _limpar_plano(brutos: list, ficha: dict, video: str, imagem_padrao=None) -> tuple:
    """Passos validos e o que ficou de fora (em frases para o dono).

    `imagem_padrao` sao as imagens que o dono ARRASTOU para a janela (uma ou
    uma lista): passo de imagem cujo arquivo nao foi achado pelo nome ("coloca
    ESSA imagem") pega a proxima delas que ainda nao foi usada.
    """
    passos, avisos = [], []
    pasta = Path(video).parent
    dur = float(ficha.get("duracao") or 0.0)
    unicos = {}
    if isinstance(imagem_padrao, str):
        imagem_padrao = [imagem_padrao] if imagem_padrao else []
    arrastadas = list(imagem_padrao or [])
    usadas: set = set()
    for b in brutos or []:
        tipo = _sem_acento(b.get("tipo") or "").replace(" ", "_")
        if tipo == "formato":
            prop = _proporcao(b.get("proporcao") or b.get("formato") or "")
            if not prop:
                continue
            modo = _sem_acento(b.get("modo") or "desfocado")
            modo = ("preencher" if any(k in modo for k in ("preench", "cort", "crop", "cheia", "zoom"))
                    else "barras" if any(k in modo for k in ("barra", "tarja", "pad", "pret"))
                    else "desfocado")
            foco = _sem_acento(b.get("foco") or "centro")
            foco = foco if foco in ("centro", "esquerda", "direita") else "centro"
            unicos["formato"] = {"tipo": "formato", "proporcao": prop, "modo": modo, "foco": foco}
        elif tipo in ("cortar_silencio", "silencio", "cortar_silencios"):
            unicos["cortar_silencio"] = {"tipo": "cortar_silencio",
                                         "silencio_minimo": _num(b.get("silencio_minimo"), 0.5, 0.2, 5.0)}
        elif tipo in ("manter_trecho", "remover_trecho"):
            a = _num(b.get("inicio"), 0.0, 0.0, dur)
            f = _num(b.get("fim"), dur, 0.0, dur)
            if a is None or f is None or f - a < 0.2:
                avisos.append("um dos trechos veio sem inicio e fim que batam com o video")
                continue
            passos.append({"tipo": tipo, "inicio": round(a, 3), "fim": round(f, 3)})
        elif tipo in ("espelhar", "virar", "ao_contrario"):
            unicos[tipo] = {"tipo": tipo}
        elif tipo == "girar":
            g = int(_num(b.get("graus"), 90) or 90)
            g = {270: -90, -270: 90}.get(g, g)
            if g in (90, -90, 180):
                unicos["girar"] = {"tipo": "girar", "graus": g}
        elif tipo == "velocidade":
            f = _num(b.get("fator"), 1.0, 0.25, 4.0)
            if f and abs(f - 1.0) > 0.01:
                unicos["velocidade"] = {"tipo": "velocidade", "fator": round(f, 3)}
        elif tipo == "texto":
            txt = str(b.get("texto") or "").strip()[:220]
            if not txt:
                continue
            pos = _sem_acento(b.get("posicao") or "topo")
            pos = pos if pos in ("topo", "centro", "baixo") else "topo"
            tam = _sem_acento(b.get("tamanho") or "medio")
            tam = tam if tam in ("pequeno", "medio", "grande") else "medio"
            cor = _sem_acento(b.get("cor") or "branco")
            cor = cor if cor in _CORES or re.fullmatch(r"#?[0-9a-f]{6}", cor) else "branco"
            passos.append({"tipo": "texto", "texto": txt, "posicao": pos, "tamanho": tam,
                           "cor": cor, "fundo": bool(b.get("fundo", False)),
                           "inicio": _num(b.get("inicio"), None), "fim": _num(b.get("fim"), None)})
        elif tipo == "imagem":
            dito = b.get("arquivo") or b.get("imagem")
            caminho = ""
            if arrastadas:
                # Primeiro entre as ARRASTADAS: o nome dito pode bater com uma
                # delas, e ai nao se sai procurando no disco (onde um arquivo
                # mais novo de nome parecido roubaria o lugar).
                d = str(dito or "").strip().strip('"')
                for c in arrastadas:
                    if d and os.path.normcase(d) == os.path.normcase(c):
                        caminho = c
                        break
                if not caminho:
                    chaves = [w for w in _palavras(Path(d).stem if d else "") if w not in _GENERICAS]
                    for c in arrastadas:
                        if chaves and all(w in " ".join(_palavras(Path(c).stem)) for w in chaves):
                            caminho = c
                            break
            # Com imagens arrastadas, "a imagem do site" e DESCRICAO de uma
            # delas, nao nome de arquivo: procurar no disco acharia um
            # "site.png" qualquer. So vai ao disco o que parece nome de arquivo.
            if not caminho and (not arrastadas or re.search(r"\.[a-z0-9]{2,4}$|[\\/]", str(dito or ""), re.I)):
                caminho = achar_arquivo(dito, EXT_IMAGEM, pasta)
            if not caminho and arrastadas:
                livres = [c for c in arrastadas if c not in usadas]
                caminho = (livres or arrastadas)[0]
            if not caminho:
                avisos.append(_faltou_arquivo("imagem", dito))
                continue
            pos = _sem_acento(b.get("posicao") or "canto_superior_direito").replace(" ", "_")
            pos = _SINONIMOS_POSICAO.get(pos, pos)
            pos = pos if pos in _POSICOES_IMG else "canto_superior_direito"
            if any(q["tipo"] == "imagem" and q["arquivo"] == caminho and q["posicao"] == pos
                   for q in passos):
                continue                         # o mesmo passo repetido
            usadas.add(caminho)
            passos.append({"tipo": "imagem", "arquivo": caminho, "posicao": pos,
                           "tamanho": _num(b.get("tamanho"), 25, 3, 100),
                           "opacidade": _num(b.get("opacidade"), 1.0, 0.1, 1.0),
                           "inicio": _num(b.get("inicio"), None), "fim": _num(b.get("fim"), None)})
        elif tipo == "legenda":
            estilo = _sem_acento(b.get("estilo") or "tiktok")
            idioma = _sem_acento(b.get("idioma") or "pt")[:2]
            unicos["legenda"] = {"tipo": "legenda",
                                 "estilo": "simples" if "simples" in estilo else "tiktok",
                                 "idioma": idioma if idioma in ("pt", "en", "es") else "pt"}
        elif tipo == "musica":
            caminho = achar_arquivo(b.get("arquivo") or b.get("musica"), EXT_AUDIO, pasta)
            if not caminho:
                avisos.append(_faltou_arquivo("musica", b.get("arquivo") or b.get("musica")))
                continue
            unicos["musica"] = {"tipo": "musica", "arquivo": caminho,
                                "volume": _num(b.get("volume"), 0.15, 0.02, 1.0)}
        elif tipo == "som":
            acao = _sem_acento(b.get("acao") or "")
            acao = ("mudo" if "mud" in acao or "tir" in acao else
                    "aumentar" if "aument" in acao or "alto" in acao else
                    "diminuir" if "diminu" in acao or "baix" in acao else
                    "normalizar" if "normal" in acao or "equilib" in acao else "")
            if acao:
                padrao = {"aumentar": 1.6, "diminuir": 0.6}.get(acao, 1.0)
                unicos["som"] = {"tipo": "som", "acao": acao,
                                 "fator": _num(b.get("fator"), padrao, 0.05, 6.0)}
        elif tipo == "efeito":
            nome = _sem_acento(b.get("nome") or "").strip()
            nome = _SINONIMOS_EFEITO.get(nome, nome.replace(" ", "_"))
            if nome in _EFEITOS:
                unicos["efeito:" + nome] = {"tipo": "efeito", "nome": nome,
                                            "intensidade": _num(b.get("intensidade"), 1.0, 0.3, 2.0)}
            else:
                avisos.append(f"o efeito '{nome[:30]}' eu nao conheco")
        elif tipo == "transicao":
            unicos["transicao"] = {"tipo": "transicao",
                                   "entrada": _num(b.get("entrada"), 1.0, 0.0, 5.0),
                                   "saida": _num(b.get("saida"), 1.0, 0.0, 5.0)}
        elif tipo == "juntar":
            lista = b.get("arquivos") or b.get("videos") or []
            if isinstance(lista, str):
                lista = [lista]
            achados = []
            for item in lista[:6]:
                c = achar_arquivo(item, EXT_VIDEO, pasta)
                if c and os.path.normcase(c) != os.path.normcase(video):
                    achados.append(c)
                else:
                    avisos.append(f"nao achei o video '{str(item)[:40]}' para juntar")
            if achados:
                unicos["juntar"] = {"tipo": "juntar", "arquivos": achados,
                                    "posicao": "antes" if "antes" in _sem_acento(b.get("posicao") or "") else "depois"}
        elif tipo == "qualidade":
            nivel = _sem_acento(b.get("nivel") or "normal")
            nivel = "leve" if any(k in nivel for k in ("leve", "whats", "baix", "pequen")) else \
                    "alta" if "alta" in nivel or "max" in nivel else "normal"
            unicos["qualidade"] = {"tipo": "qualidade", "nivel": nivel}
        elif tipo == "filtro_livre":
            filtro = str(b.get("video") or b.get("filtro") or "").strip()
            if filtro and len(filtro) <= 300 and not _PROIBIDO_NO_FILTRO_LIVRE.search(filtro):
                passos.append({"tipo": "filtro_livre", "video": filtro})
            elif filtro:
                avisos.append("um efeito especial pedido nao era seguro e ficou de fora")
        elif tipo in ("salvar", "destino", "pasta"):
            onde = _sem_acento(b.get("onde") or b.get("pasta") or "").replace(" ", "_")
            onde = ("area_de_trabalho" if any(k in onde for k in ("area", "desktop", "trabalho"))
                    else "downloads" if "download" in onde
                    else "videos" if "video" in onde
                    else "documentos" if "document" in onde else "")
            if onde in _ONDE_SALVAR:
                unicos["salvar"] = {"tipo": "salvar", "onde": onde}
    passos = list(unicos.values()) + passos
    return passos, avisos


_FALA_DE_IMAGEM = re.compile(r"\b(imagem|imagens|foto|fotos|logo|logos|logotipo|figura|figuras|"
                             r"banner|banners|arte|artes|print|prints)\b")
_PLURAL_DE_IMAGEM = re.compile(r"\b(imagens|fotos|logos|figuras|banners|artes|prints|as duas|"
                               r"os dois|as tres|todas)\b")


def _garantir_imagens(passos: list, pedido: str, imagens: list, imagem: str,
                      tem_formato: bool) -> None:
    """As imagens pedidas nao podem sumir no caminho do cerebro.

    "coloca essa imagem" sem passo de imagem no plano: a da janela entra no
    canto de cima, como logo. "Essas duas imagens" / "as imagens": TODAS as
    arrastadas entram; as que o plano esqueceu vao para os lugares livres.
    """
    if not imagens:
        return
    t = " ".join(_palavras(pedido))
    if not (_FALA_DE_IMAGEM.search(t) or imagem):
        return
    plural = bool(_PLURAL_DE_IMAGEM.search(t))
    ja = [q for q in passos if q["tipo"] == "imagem"]
    usadas = {os.path.normcase(q["arquivo"]) for q in ja}
    if plural:
        faltam = [c for c in imagens if os.path.normcase(c) not in usadas]
    elif not ja:
        faltam = [imagem or imagens[-1]]
    else:
        faltam = []
    ocupadas = {q["posicao"] for q in ja}
    ordem = ((["faixa_de_cima", "faixa_de_baixo"] if tem_formato and len(ja) + len(faltam) >= 2 else [])
             + ["canto_superior_direito", "canto_superior_esquerdo", "canto_inferior_direito",
                "canto_inferior_esquerdo"])
    for c in faltam:
        pos = next((x for x in ordem if x not in ocupadas), "centro")
        ocupadas.add(pos)
        passos.append({"tipo": "imagem", "arquivo": c, "posicao": pos, "tamanho": 25.0,
                       "opacidade": 1.0, "inicio": None, "fim": None})


def _faltou_arquivo(o_que: str, nome) -> str:
    """O aviso de arquivo nao achado, dito do jeito que o dono resolve."""
    chaves = [w for w in _palavras(str(nome or "")) if w not in _GENERICAS]
    artigo = "a" if o_que in ("imagem", "musica") else "o"
    if not chaves:
        return (f"faltou dizer qual {o_que}: me fala o nome do arquivo, ou poe ele "
                f"na mesma pasta do video")
    return f"nao achei {artigo} {o_que} '{str(nome)[:40]}' nas suas pastas"


def _achar(passos: list, tipo: str):
    for p in passos:
        if p.get("tipo") == tipo:
            return p
    return None


def _resumo(passos: list) -> str:
    """O que vai ser feito, em portugues de gente, na ordem que importa."""
    partes = []
    if _achar(passos, "juntar"):
        partes.append("juntar os videos")
    f = _achar(passos, "formato")
    if f:
        nome = {"9:16": "no formato do TikTok (9:16)", "16:9": "no formato do YouTube (16:9)",
                "1:1": "quadrado (1:1)", "4:5": "no formato do feed (4:5)"}[f["proporcao"]]
        jeito = {"desfocado": "com fundo desfocado", "preencher": "enchendo a tela",
                 "barras": "com tarja preta"}[f["modo"]]
        partes.append(f"deixar {nome} {jeito}")
    if _achar(passos, "cortar_silencio"):
        partes.append("cortar os silencios")
    if any(p["tipo"] == "manter_trecho" for p in passos):
        partes.append("deixar so o trecho pedido")
    if any(p["tipo"] == "remover_trecho" for p in passos):
        partes.append("tirar o trecho pedido")
    if _achar(passos, "espelhar"):
        partes.append("espelhar")
    if _achar(passos, "virar"):
        partes.append("virar de cabeca para baixo")
    if _achar(passos, "girar"):
        partes.append("girar")
    v = _achar(passos, "velocidade")
    if v:
        partes.append("acelerar" if v["fator"] > 1 else "deixar em camera lenta")
    if _achar(passos, "legenda"):
        partes.append("colocar legenda")
    n_txt = sum(1 for p in passos if p["tipo"] == "texto")
    if n_txt:
        partes.append("colocar o texto" if n_txt == 1 else "colocar os textos")
    imgs = [p for p in passos if p["tipo"] == "imagem"]
    if imgs:
        lugares = {p["posicao"] for p in imgs}
        if len(imgs) == 2 and lugares == {"faixa_de_cima", "faixa_de_baixo"}:
            partes.append("colocar uma imagem em cima e a outra embaixo do video")
        else:
            partes.append("colocar a imagem" if len(imgs) == 1 else f"colocar as {len(imgs)} imagens")
    if _achar(passos, "musica"):
        partes.append("colocar a musica de fundo")
    s = _achar(passos, "som")
    if s:
        partes.append({"mudo": "tirar o som", "aumentar": "aumentar o volume",
                       "diminuir": "abaixar o volume", "normalizar": "equilibrar o som"}[s["acao"]])
    efeitos = [p["nome"] for p in passos if p["tipo"] == "efeito"]
    if efeitos:
        nomes = {"zoom_nos_cortes": "zoom nos cortes", "zoom_lento": "zoom lento",
                 "preto_e_branco": "preto e branco", "cores_vivas": "cores vivas",
                 "tremer": "tela tremendo"}
        partes.append("efeito de " + ", ".join(nomes.get(e, e.replace("_", " ")) for e in efeitos))
    if _achar(passos, "transicao"):
        partes.append("fazer a transicao no comeco e no fim")
    if _achar(passos, "ao_contrario"):
        partes.append("deixar de tras para frente")
    q = _achar(passos, "qualidade")
    if q and q["nivel"] == "leve":
        partes.append("deixar leve para o WhatsApp")
    elif q and q["nivel"] == "alta":
        partes.append("caprichar na qualidade, em Full HD")
    if any(p["tipo"] == "filtro_livre" for p in passos):
        partes.append("aplicar o efeito especial")
    sv = _achar(passos, "salvar")
    if sv and partes:
        partes.append({"area_de_trabalho": "salvar na sua area de trabalho",
                       "downloads": "salvar nos seus Downloads", "videos": "salvar na pasta Videos",
                       "documentos": "salvar nos seus Documentos"}.get(sv["onde"], "salvar"))
    if not partes:
        return ""
    if len(partes) == 1:
        return partes[0]
    return ", ".join(partes[:-1]) + " e " + partes[-1]


# ═════════════════════════════════════════════════════════════════════════════
#  O SOM: onde tem fala (corte de silencio) e o que foi dito (legenda)
# ═════════════════════════════════════════════════════════════════════════════

def _envelope_db(ff: str, video: str, cancelado) -> list:
    """Volume em dBFS a cada 10 ms, lendo o audio em pedacos (memoria baixa)."""
    import numpy as np  # noqa: PLC0415
    cmd = [ff, "-v", "error", "-nostdin", "-i", str(video), "-vn", "-ac", "1",
           "-ar", "16000", "-f", "s16le", "pipe:1"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            stdin=subprocess.DEVNULL,
                            creationflags=_SEM_JANELA | _PRIORIDADE_BAIXA)
    quadro = 160 * 2          # 10 ms de 16 kHz, 16 bits
    bloco = quadro * 2000     # 20 s por leitura
    sobra = b""
    niveis = []
    try:
        while True:
            if cancelado():
                proc.kill()
                break
            dados = proc.stdout.read(bloco)
            if not dados:
                break
            dados = sobra + dados
            n = len(dados) // quadro
            sobra = dados[n * quadro:]
            if n == 0:
                continue
            a = np.frombuffer(dados[:n * quadro], dtype=np.int16).astype(np.float32)
            a = a.reshape(n, 160)
            rms = np.sqrt((a * a).mean(axis=1))
            niveis.append(20.0 * np.log10(rms / 32768.0 + 1e-9))
    finally:
        try:
            proc.wait(timeout=10)
        except Exception:
            pass
    if not niveis:
        return []
    return np.concatenate(niveis).tolist()


def _trechos_com_fala(db: list, dur: float, silencio_minimo: float,
                      folga: float = 0.15) -> tuple:
    """Pedacos (inicio, fim) em que ha som de fala. (None, motivo) se nao der.

    LIMIAR QUE SE ADAPTA AO VIDEO. Nos videos do Diego (microfone bom, sala
    quieta) o corte certo foi -50 dBFS. Video de cliente e gravado no celular,
    na rua, com ventilador: ali o "silencio" fica em -40. Por isso o limiar e
    medido no proprio video — o chao (percentil 10) e a voz (percentil 95) —
    e fica a 30% do caminho entre os dois. Num trecho real dele (10/09/2026)
    isso deu -45,5 dBFS com a voz em -11: nenhum pedaco cortado passou de
    -47,7 dBFS, e as duas unicas "palavras" do whisper dentro de um corte eram
    o proprio whisper esticando a palavra por cima de silencio digital.
    Errar para o lado de GUARDAR e de proposito: pausa que fica e so um
    respiro; fala cortada estraga o video.
    """
    import numpy as np  # noqa: PLC0415
    if not db:
        return None, "o video nao tem som para eu achar os silencios"
    arr = np.asarray(db, dtype=np.float32)
    vivos = arr[arr > -90]
    if vivos.size < 50:
        return None, "o video esta mudo, entao nao ha silencio para cortar"
    # SILENCIO DIGITAL CONTA COMO SILENCIO. Som zerado (-180 dBFS: gravacao de
    # tela sem microfone, trecho mutado) entra na conta preso em -90. Antes ele
    # era jogado fora e, num video so de fala e zeros, a conta via "som
    # continuo" e nao cortava nada (achado no teste de 10 min, 10/09/2026).
    presos = np.maximum(arr, -90.0)
    chao = float(np.percentile(presos, 10))
    voz = float(np.percentile(presos, 95))
    if voz - chao < 10:
        return None, "o som do video e continuo (musica ou barulho o tempo todo), sem pausa clara"
    limiar = max(chao + 0.30 * (voz - chao), -60.0)
    ativo = arr > limiar
    # ilhas de som
    ilhas = []
    i, n = 0, len(ativo)
    while i < n:
        if ativo[i]:
            j = i
            while j < n and ativo[j]:
                j += 1
            ilhas.append([i, j])
            i = j
        else:
            i += 1
    # estalo isolado (< 50 ms) longe de fala nao e fala. So os vizinhos contam:
    # as ilhas vem em ordem, e comparar todas com todas travaria num video longo.
    filtradas = []
    for k, (a, b) in enumerate(ilhas):
        perto_antes = k > 0 and a - ilhas[k - 1][1] < 30
        perto_depois = k + 1 < len(ilhas) and ilhas[k + 1][0] - b < 30
        if b - a >= 5 or perto_antes or perto_depois:
            filtradas.append([a, b])
    ilhas = filtradas
    if not ilhas:
        return None, "nao achei fala no video"
    # emenda pausas menores que o minimo; so as maiores sao cortadas
    minimo_q = int(round(silencio_minimo * 100))
    unidas = [ilhas[0][:]]
    for a, b in ilhas[1:]:
        if a - unidas[-1][1] < minimo_q:
            unidas[-1][1] = b
        else:
            unidas.append([a, b])
    f = int(round(folga * 100))
    trechos = []
    for a, b in unidas:
        ta = max(0.0, (a - f) / 100.0)
        tb = min(dur, (b + f) / 100.0)
        if trechos and ta <= trechos[-1][1]:
            trechos[-1] = (trechos[-1][0], max(trechos[-1][1], tb))
        else:
            trechos.append((ta, tb))
    return trechos, f"limiar {limiar:.1f} dBFS (chao {chao:.1f}, voz {voz:.1f})"


def _intersecao(a: list, b: list) -> list:
    saida, i, j = [], 0, 0
    while i < len(a) and j < len(b):
        ini, fim = max(a[i][0], b[j][0]), min(a[i][1], b[j][1])
        if fim - ini > 1e-3:
            saida.append((ini, fim))
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return saida


def _subtrair(segs: list, ini: float, fim: float) -> list:
    saida = []
    for a, b in segs:
        if fim <= a or ini >= b:
            saida.append((a, b))
            continue
        if ini > a:
            saida.append((a, ini))
        if fim < b:
            saida.append((fim, b))
    return saida


def _uniao(segs: list) -> list:
    saida = []
    for a, b in sorted(segs):
        if saida and a <= saida[-1][1]:
            saida[-1] = (saida[-1][0], max(saida[-1][1], b))
        else:
            saida.append((a, b))
    return saida


_ALUCINACOES = ("obrigado por assistir", "inscreva-se", "se inscreva", "legendas pela comunidade",
                "legendado por", "amara.org", "deixe seu like", "ate a proxima")


def _transcrever(ff: str, video: str, ouvido, idioma: str, ini: float, fim: float,
                 cancelado, log) -> list:
    """[(inicio, fim, palavra)] no tempo do video ORIGINAL.

    O whisper e o do proprio ouvido do JARVIS (emprestado — carregar outro
    custaria mais 0,5 a 3 GB). Aqui ele serve SO para o texto da legenda; o
    corte de silencio nunca olha para ele.
    """
    import numpy as np  # noqa: PLC0415
    cmd = [ff, "-v", "error", "-nostdin", "-ss", f"{ini:.3f}", "-t", f"{max(0.5, fim - ini):.3f}",
           "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "pipe:1"]
    r = subprocess.run(cmd, capture_output=True, timeout=max(120, (fim - ini) * 2),
                       creationflags=_SEM_JANELA | _PRIORIDADE_BAIXA)
    amostras = np.frombuffer(r.stdout, dtype=np.float32)
    if amostras.size < 16000 * 0.5 or cancelado():
        return []
    modelo = ouvido() if callable(ouvido) else None
    if modelo is None:
        from faster_whisper import WhisperModel  # noqa: PLC0415
        local = _RAIZ / "models" / "whisper-small"
        modelo = WhisperModel(str(local) if (local / "model.bin").exists() else "small",
                              device="cpu", compute_type="int8")
    na_placa = "cuda" in str(getattr(getattr(modelo, "model", None), "device", "")).lower()
    segs, _info = modelo.transcribe(
        amostras, language=idioma or "pt", word_timestamps=True,
        beam_size=5 if na_placa else 2, condition_on_previous_text=False,
        vad_filter=False)
    palavras = []
    for s in segs:
        if cancelado():
            break
        texto_seg = _sem_acento(getattr(s, "text", ""))
        if any(a in texto_seg for a in _ALUCINACOES):
            continue
        if getattr(s, "no_speech_prob", 0) > 0.6 and getattr(s, "avg_logprob", 0) < -1.0:
            continue
        for w in (getattr(s, "words", None) or []):
            txt = str(getattr(w, "word", "")).strip()
            if txt:
                palavras.append((ini + float(w.start), ini + float(w.end), txt))
    log(f"[editar_video] legenda: {len(palavras)} palavras")
    return palavras


def _mapear_palavras(palavras: list, segs: list, vel: float) -> list:
    """Tempo do ORIGINAL -> tempo do video PRONTO (depois dos cortes)."""
    saida, desloc = [], []
    acc = 0.0
    for a, b in segs:
        desloc.append(acc)
        acc += b - a
    for ini, fim, txt in palavras:
        meio = (ini + fim) / 2
        for k, (a, b) in enumerate(segs):
            if a <= meio < b:
                n_ini = (desloc[k] + max(ini, a) - a) / vel
                n_fim = (desloc[k] + min(fim, b) - a) / vel
                if n_fim > n_ini:
                    saida.append((n_ini, n_fim, txt))
                break
    return saida


# ═════════════════════════════════════════════════════════════════════════════
#  LEGENDA E TEXTO: um arquivo .ass queimado pela libass
# ═════════════════════════════════════════════════════════════════════════════

def _tempo_ass(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _cor_ass(nome: str) -> str:
    """Cor para a marca \\c do .ass: &HBBGGRR& (azul-verde-vermelho, sem alfa)."""
    hexa = _CORES.get(nome) or str(nome).lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", hexa or ""):
        hexa = "FFFFFF"
    r, g, b = hexa[0:2], hexa[2:4], hexa[4:6]
    return f"&H{b}{g}{r}&".upper()


def _limpo_ass(texto: str) -> str:
    return (str(texto).replace("\\", "/").replace("{", "(").replace("}", ")")
            .replace("\r", " ").replace("\n", " ").strip())


def _quebrar(texto: str, max_chars: int) -> str:
    linhas, atual = [], ""
    for p in texto.split():
        if atual and len(atual) + 1 + len(p) > max_chars:
            linhas.append(atual)
            atual = p
        else:
            atual = (atual + " " + p).strip()
    if atual:
        linhas.append(atual)
    return "\\N".join(linhas[:4])


def _fontes(pasta: Path) -> tuple:
    """Copia as fontes do Windows que a legenda usa e escolhe a familia."""
    pasta.mkdir(parents=True, exist_ok=True)
    win = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    copiadas = []
    for nome in ("ariblk.ttf", "arialbd.ttf", "arial.ttf", "seguibl.ttf", "segoeuib.ttf"):
        origem = win / nome
        if origem.is_file():
            try:
                shutil.copy2(origem, pasta / nome)
                copiadas.append(nome)
            except Exception:
                pass
    grossa = "Arial Black" if "ariblk.ttf" in copiadas else "Arial"
    normal = "Arial" if ("arialbd.ttf" in copiadas or "arial.ttf" in copiadas) else grossa
    return grossa, normal


def _montar_ass(W: int, H: int, dur: float, palavras: list, estilo: str,
                textos: list, fonte_grossa: str, fonte_normal: str,
                ajustes: dict | None = None) -> str:
    """`ajustes` vem do `_montar` quando ha imagem nas faixas: a legenda e os
    textos sobem (ou descem) para dentro do video, em vez de cair em cima dela."""
    aj = ajustes or {}
    base = min(W, H)
    vertical = H > W * 1.2
    fs_leg = round(base * (0.072 if estilo == "tiktok" else 0.052))
    mv_leg = int(aj.get("mv_legenda") or round(H * (0.26 if vertical else 0.08)))
    contorno = max(2, round(fs_leg * 0.09))
    margem = round(W * 0.06)
    linhas = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}",
        "WrapStyle: 2", "ScaledBorderAndShadow: yes", "YCbCr Matrix: TV.709", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Legenda,{fonte_grossa if estilo == 'tiktok' else fonte_normal},{fs_leg},"
        f"&H00FFFFFF,&H00FFFFFF,&H00000000,&H78000000,-1,0,0,0,100,100,0,0,1,{contorno},"
        f"{max(1, round(fs_leg * 0.04))},2,{margem},{margem},{mv_leg},1",
        f"Style: Texto,{fonte_normal},{round(base * 0.07)},&H00FFFFFF,&H00FFFFFF,&H00000000,"
        f"&H78000000,-1,0,0,0,100,100,0,0,1,{max(2, round(base * 0.006))},2,5,{margem},{margem},0,1",
        f"Style: TextoCaixa,{fonte_normal},{round(base * 0.07)},&H00FFFFFF,&H00FFFFFF,&H96000000,"
        f"&H00000000,-1,0,0,0,100,100,0,0,3,{max(6, round(base * 0.018))},0,5,{margem},{margem},0,1",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    # ── legenda automatica ──
    if palavras:
        max_chars = (16 if vertical else 26) if estilo == "tiktok" else (34 if vertical else 48)
        max_pal = 3 if estilo == "tiktok" else 9
        blocos, atual = [], []
        for w in palavras:
            quebra = False
            if atual:
                txt_atual = " ".join(x[2] for x in atual)
                if (len(txt_atual) + 1 + len(w[2]) > max_chars or len(atual) >= max_pal
                        or w[0] - atual[-1][1] > 0.45 or w[1] - atual[0][0] > 3.2):
                    quebra = True
            if quebra:
                blocos.append(atual)
                atual = []
            atual.append(w)
        if atual:
            blocos.append(atual)
        for k, bloco in enumerate(blocos):
            prox = blocos[k + 1][0][0] if k + 1 < len(blocos) else dur
            fim_bloco = min(bloco[-1][1] + 0.25, prox, dur)
            if estilo == "tiktok":
                for j, w in enumerate(bloco):
                    ini = w[0]
                    fim = bloco[j + 1][0] if j + 1 < len(bloco) else fim_bloco
                    if fim - ini < 0.05:
                        fim = ini + 0.05
                    partes = []
                    for q, x in enumerate(bloco):
                        t = _limpo_ass(x[2]).upper()
                        partes.append("{\\c&H00E5FF&}" + t + "{\\r}" if q == j else t)
                    linhas.append(f"Dialogue: 0,{_tempo_ass(ini)},{_tempo_ass(fim)},Legenda,,0,0,0,,"
                                  + _quebrar(" ".join(partes), 999))
            else:
                txt = _quebrar(_limpo_ass(" ".join(x[2] for x in bloco)), max_chars)
                linhas.append(f"Dialogue: 0,{_tempo_ass(bloco[0][0])},{_tempo_ass(fim_bloco)},"
                              f"Legenda,,0,0,0,,{txt}")
    # ── textos pedidos ──
    for t in textos:
        tam = {"pequeno": 0.05, "medio": 0.07, "grande": 0.095}.get(t["tamanho"], 0.07)
        fs = round(base * tam)
        ini, fim = _janela(t.get("inicio"), t.get("fim"), dur)
        if t["posicao"] == "topo":
            pos = f"\\an8\\pos({W // 2},{int(aj.get('topo_y') or round(H * (0.1 if vertical else 0.06)))})"
        elif t["posicao"] == "baixo":
            pos = f"\\an2\\pos({W // 2},{int(aj.get('baixo_y') or round(H * (0.8 if vertical else 0.93)))})"
        else:
            pos = f"\\an5\\pos({W // 2},{H // 2})"
        max_chars = max(8, int(W * 0.86 / (fs * 0.56)))
        corpo = _quebrar(_limpo_ass(t["texto"]), max_chars)
        estilo_t = "TextoCaixa" if t.get("fundo") else "Texto"
        linhas.append(f"Dialogue: 1,{_tempo_ass(ini)},{_tempo_ass(fim)},{estilo_t},,0,0,0,,"
                      f"{{{pos}\\fs{fs}\\c{_cor_ass(t['cor'])}\\fad(200,200)}}{corpo}")
    return "\n".join(linhas) + "\n"


def _janela(inicio, fim, dur: float) -> tuple:
    """(inicio, fim) no video pronto; negativo conta do fim; vazio = tudo."""
    a = 0.0 if inicio is None else float(inicio)
    b = dur if fim is None else float(fim)
    if a < 0:
        a = max(0.0, dur + a)
    if b < 0:
        b = max(0.0, dur + b)
    if b <= 0 or b > dur:
        b = dur
    a = min(max(0.0, a), max(0.0, dur - 0.1))
    if b - a < 0.1:
        b = min(dur, a + 3.0)
    return a, b


# ═════════════════════════════════════════════════════════════════════════════
#  O GRAFO DE FILTROS
# ═════════════════════════════════════════════════════════════════════════════

class _Grafo:
    def __init__(self):
        self.linhas: list = []
        self.n = 0

    def rotulo(self, prefixo: str = "x") -> str:
        self.n += 1
        return f"{prefixo}{self.n}"

    def add(self, entrada, filtros: str, saida: str | None = None) -> str:
        saida = saida or self.rotulo()
        entradas = entrada if isinstance(entrada, list) else [entrada]
        self.linhas.append("".join(f"[{e}]" for e in entradas) + filtros + f"[{saida}]")
        return saida

    def texto(self) -> str:
        return ";\n".join(self.linhas)


def _atempo(fator: float) -> str:
    partes = []
    f = float(fator)
    while f > 2.0:
        partes.append("atempo=2.0")
        f /= 2.0
    while f < 0.5:
        partes.append("atempo=0.5")
        f /= 0.5
    partes.append(f"atempo={f:.4f}")
    return ",".join(partes)


def _filtro_de_efeito(nome: str, k: float, W: int, H: int, D: float, disp: set) -> str:
    k = float(k or 1.0)
    tabela = {
        "preto_e_branco": "hue=s=0",
        "sepia": "colorchannelmixer=.393:.769:.189:0:.349:.686:.168:0:.272:.534:.131",
        "vintage": "curves=preset=vintage,vignette=PI/5",
        "cores_vivas": f"eq=saturation={1 + 0.4 * k:.2f}:contrast={1 + 0.05 * k:.2f}",
        "desbotado": f"eq=saturation={max(0.15, 1 - 0.4 * k):.2f}",
        "clarear": f"eq=brightness={0.07 * k:.3f}",
        "escurecer": f"eq=brightness={-0.08 * k:.3f}",
        "contraste": f"eq=contrast={1 + 0.25 * k:.2f}",
        "nitidez": f"unsharp=5:5:{0.8 * k:.2f}",
        "desfoque": f"gblur=sigma={4 * k:.1f}",
        "vinheta": "vignette=PI/4" if k <= 1 else "vignette=PI/3.2",
        "quente": f"colorbalance=rs={0.08 * k:.3f}:gs={0.02 * k:.3f}:bs={-0.08 * k:.3f}",
        "frio": f"colorbalance=rs={-0.08 * k:.3f}:bs={0.08 * k:.3f}",
        "negativo": "negate",
        "cinema": (f"drawbox=x=0:y=0:w=iw:h=ih*0.11:color=black:t=fill,"
                   f"drawbox=x=0:y=ih-ih*0.11:w=iw:h=ih*0.11:color=black:t=fill"),
        # Camera tremendo: uma janela 5% menor passeia pelo quadro em senos de
        # frequencias diferentes (nao repete de forma obvia) e volta ao tamanho.
        "tremer": (f"crop=w=iw*{1 - 0.05 * min(k, 1.6):.3f}:h=ih*{1 - 0.05 * min(k, 1.6):.3f}:"
                   f"x='(iw-ow)/2*(1+sin(t*23)*0.8)':y='(ih-oh)/2*(1+cos(t*19)*0.8)',"
                   f"scale={W}:{H},setsar=1"),
        "zoom_lento": (f"scale=w='trunc({W}*(1+{0.12 * k:.3f}*t/{max(D, 1):.3f})/2)*2':"
                       f"h='trunc({H}*(1+{0.12 * k:.3f}*t/{max(D, 1):.3f})/2)*2':eval=frame,"
                       f"crop={W}:{H}"),
    }
    f = tabela.get(nome, "")
    if not f:
        return ""
    primeiro = re.match(r"[a-z0-9_]+", f).group(0)
    return f if (not disp or primeiro in disp) else ""


def _posicao_img(pos: str, m: int) -> tuple:
    return {
        "canto_superior_direito": (f"W-w-{m}", f"{m}"),
        "canto_superior_esquerdo": (f"{m}", f"{m}"),
        "canto_inferior_direito": (f"W-w-{m}", f"H-h-{m}"),
        "canto_inferior_esquerdo": (f"{m}", f"H-h-{m}"),
        "centro": ("(W-w)/2", "(H-h)/2"),
        "topo": ("(W-w)/2", f"{m}"),
        "baixo": ("(W-w)/2", f"H-h-{m}"),
        "tela_cheia": ("0", "0"),
    }.get(pos, (f"W-w-{m}", f"{m}"))


def _montar(passos: list, ficha: dict, segs: list, tem_ass: bool, entrada: str,
            disp: set) -> dict:
    """Receita limpa -> entradas, grafo de filtros, duracao e tamanho finais."""
    g = _Grafo()
    entradas = ["-i", str(entrada)]
    n_in = 1
    W0, H0 = int(ficha["largura"]), int(ficha["altura"])
    fps = _fps_de_saida(float(ficha.get("fps") or 30))
    tem_audio = bool(ficha.get("tem_audio"))
    dur_in = float(ficha["duracao"])
    avisos = []

    v = g.add("0:v", f"fps={fps},setsar=1")
    a = g.add("0:a", "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo") \
        if tem_audio else None

    # ── linha do tempo (cortes) ──
    zoom_cortes = _achar(passos, "efeito") and any(
        p["tipo"] == "efeito" and p["nome"] == "zoom_nos_cortes" for p in passos)
    k_zoom = next((p["intensidade"] for p in passos if p["tipo"] == "efeito"
                   and p["nome"] == "zoom_nos_cortes"), 1.0)
    inteiro = len(segs) == 1 and segs[0][0] < 0.01 and segs[0][1] > dur_in - 0.01
    if not inteiro:
        n = len(segs)
        z = 1.0 + 0.12 * float(k_zoom)
        Wz, Hz = _par(W0 * z), _par(H0 * z)
        if n == 1:
            a0, b0 = segs[0]
            v = g.add(v, f"trim=start={a0:.3f}:end={b0:.3f},setpts=PTS-STARTPTS")
            if a:
                a = g.add(a, f"atrim=start={a0:.3f}:end={b0:.3f},asetpts=PTS-STARTPTS")
        else:
            vs = [g.rotulo("vs") for _ in segs]
            g.linhas.append(f"[{v}]split={n}" + "".join(f"[{x}]" for x in vs))
            as_ = []
            if a:
                as_ = [g.rotulo("as") for _ in segs]
                g.linhas.append(f"[{a}]asplit={n}" + "".join(f"[{x}]" for x in as_))
            pares = []
            for i, (a0, b0) in enumerate(segs):
                fv = f"trim=start={a0:.3f}:end={b0:.3f},setpts=PTS-STARTPTS"
                if zoom_cortes and i % 2 == 1:
                    # `setsar=1` e obrigatorio: ampliar 12% arredonda o pixel
                    # para 1936:1935, e o concat recusa emendar pedacos com
                    # pixel de formato diferente (o video inteiro falhava).
                    fv += f",scale={Wz}:{Hz},crop={W0}:{H0},setsar=1"
                pares.append(g.add(vs[i], fv))
                if a:
                    L = b0 - a0
                    d = min(0.012, L / 4)
                    pares.append(g.add(as_[i], (
                        f"atrim=start={a0:.3f}:end={b0:.3f},asetpts=PTS-STARTPTS,"
                        f"afade=t=in:st=0:d={d:.3f},afade=t=out:st={max(0.0, L - d):.3f}:d={d:.3f}")))
            saidas = "[vcat][acat]" if a else "[vcat]"
            g.linhas.append("".join(f"[{p}]" for p in pares)
                            + f"concat=n={n}:v=1:a={1 if a else 0}{saidas}")
            v, a = "vcat", ("acat" if a else None)
    elif zoom_cortes:
        avisos.append("o zoom nos cortes precisa de corte; como nao houve, ficou de fora")

    dur = sum(b - a_ for a_, b in segs)

    # ── velocidade e tras-para-frente ──
    vel = _achar(passos, "velocidade")
    fator = float(vel["fator"]) if vel else 1.0
    if abs(fator - 1.0) > 0.01:
        v = g.add(v, f"setpts=PTS/{fator:.4f},fps={fps}")
        if a:
            a = g.add(a, _atempo(fator))
        dur = dur / fator
    if _achar(passos, "ao_contrario"):
        if dur <= 15.5:
            v = g.add(v, "reverse")
            if a:
                a = g.add(a, "areverse")
        else:
            avisos.append("de tras para frente so da em video de ate 15 segundos")

    # ── girar, virar, espelhar ──
    Wc, Hc = W0, H0
    gi = _achar(passos, "girar")
    if gi:
        if gi["graus"] == 90:
            v = g.add(v, "transpose=1")
            Wc, Hc = Hc, Wc
        elif gi["graus"] == -90:
            v = g.add(v, "transpose=2")
            Wc, Hc = Hc, Wc
        else:
            v = g.add(v, "hflip,vflip")
    if _achar(passos, "virar"):
        v = g.add(v, "vflip")
    if _achar(passos, "espelhar"):
        v = g.add(v, "hflip")

    # ── formato (a tela final) ──
    qual = _achar(passos, "qualidade")
    leve = bool(qual and qual["nivel"] == "leve")
    fmt = _achar(passos, "formato")
    rect = None                  # onde o video ficou dentro da tela final
    if fmt:
        W, H = (_PROPORCOES_LEVE if leve else _PROPORCOES)[fmt["proporcao"]]
        mesma = abs((Wc / Hc) - (W / H)) < 0.02
        if not mesma and fmt["modo"] != "preencher":
            # Encaixado (desfocado ou tarja): sobra uma faixa em cima e outra
            # embaixo (ou dos lados) — e nelas que entram as imagens de faixa.
            if Wc / Hc > W / H:
                fw, fh = W, _par(W * Hc / Wc)
            else:
                fw, fh = _par(H * Wc / Hc), H
            rect = ((W - fw) // 2, (H - fh) // 2, fw, fh)
        if mesma:
            v = g.add(v, f"scale={W}:{H},setsar=1")
        elif fmt["modo"] == "preencher":
            x = {"esquerda": "0", "direita": "iw-ow"}.get(fmt.get("foco"), "(iw-ow)/2")
            v = g.add(v, f"scale={W}:{H}:force_original_aspect_ratio=increase,"
                         f"crop={W}:{H}:{x}:(ih-oh)/2,setsar=1")
        elif fmt["modo"] == "barras":
            v = g.add(v, f"scale={W}:{H}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
                         f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1")
        else:
            # Fundo desfocado BARATO: o desfoque e feito num quadro 8x menor
            # e ampliado depois. O mesmo resultado para o olho, uma fracao do
            # processador — o PC do cliente pode ser de 2012.
            pw, ph = _par(W / 8), _par(H / 8)
            f_, b_ = g.rotulo("fg"), g.rotulo("bg")
            g.linhas.append(f"[{v}]split=2[{f_}][{b_}]")
            bb = g.add(b_, f"scale={pw}:{ph}:force_original_aspect_ratio=increase,crop={pw}:{ph},"
                           f"boxblur=4:2,scale={W}:{H},eq=brightness=-0.07")
            ff_ = g.add(f_, f"scale={W}:{H}:force_original_aspect_ratio=decrease:force_divisible_by=2")
            v = g.add([bb, ff_], "overlay=(W-w)/2:(H-h)/2,setsar=1")
    else:
        teto = 1280 if leve else 1920
        W, H = Wc, Hc
        if max(W, H) > teto:
            esc = teto / max(W, H)
            W, H = _par(W * esc), _par(H * esc)
        W, H = _par(W), _par(H)
        if (W, H) != (Wc, Hc):
            v = g.add(v, f"scale={W}:{H},setsar=1")

    # ── efeitos de cor e movimento ──
    for p in passos:
        if p["tipo"] == "efeito" and p["nome"] != "zoom_nos_cortes":
            f = _filtro_de_efeito(p["nome"], p["intensidade"], W, H, dur, disp)
            if f:
                v = g.add(v, f)
            else:
                avisos.append(f"o efeito {p['nome'].replace('_', ' ')} nao existe neste computador")
    for p in passos:
        if p["tipo"] == "filtro_livre":
            v = g.add(v, p["video"] + f",scale={W}:{H},setsar=1")

    # ── imagens por cima ──
    m = round(min(W, H) * 0.035)
    # As faixas que sobram acima e abaixo do video encaixado. Menos de 12% da
    # altura nao e faixa (e so a borda de um video quase do mesmo formato).
    faixa_cima = faixa_baixo = None
    if rect is not None:
        _x, ry, _w, rh = rect
        if ry >= H * 0.12:
            faixa_cima = (0, ry)
        if H - (ry + rh) >= H * 0.12:
            faixa_baixo = (ry + rh, H - (ry + rh))
    ajustes_ass: dict = {}
    for p in passos:
        if p["tipo"] != "imagem":
            continue
        entradas += ["-i", p["arquivo"]]
        idx = n_in
        n_in += 1
        ini, fim = _janela(p.get("inicio"), p.get("fim"), dur)
        pos = p["posicao"]
        # "Imagem grande em cima" num video vertical encaixado E a faixa.
        if pos in ("topo", "baixo") and float(p["tamanho"]) >= 60 and (
                faixa_cima if pos == "topo" else faixa_baixo):
            pos = "faixa_de_cima" if pos == "topo" else "faixa_de_baixo"
        faixa = {"faixa_de_cima": faixa_cima, "faixa_de_baixo": faixa_baixo}.get(pos)
        if pos.startswith("faixa_") and faixa is None:
            pos = "topo" if pos == "faixa_de_cima" else "baixo"     # nao ha faixa: vai por cima
        if faixa is not None:
            # A imagem cabe inteira na faixa, colada no video (um bloco so:
            # imagem, video, imagem) — longe da borda, onde o TikTok poe os
            # botoes e a descricao por cima.
            fy, fh_ = faixa
            vao = max(4, round(m * 0.6))
            bw_, bh_ = _par(W - 2 * m), _par(max(16, fh_ - vao - m))
            prep = (f"scale={bw_}:{bh_}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
                    f"format=rgba")
            x = "(W-w)/2"
            y = f"{fy + fh_ - vao}-h" if pos == "faixa_de_cima" else f"{fy + vao}"
            if pos == "faixa_de_cima":
                ajustes_ass["topo_y"] = fy + fh_ + round(m * 0.8)
            else:
                ajustes_ass["mv_legenda"] = H - fy + round(m * 0.8)
                ajustes_ass["baixo_y"] = fy - round(m * 0.8)
        elif pos == "tela_cheia":
            prep = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},format=rgba"
            x, y = _posicao_img(pos, m)
        else:
            larg = _par(W * float(p["tamanho"]) / 100.0)
            prep = f"scale={larg}:-2,format=rgba"
            x, y = _posicao_img(pos, m)
        if float(p["opacidade"]) < 0.999:
            prep += f",colorchannelmixer=aa={float(p['opacidade']):.2f}"
        img = g.add(f"{idx}:v", prep)
        v = g.add([v, img], f"overlay=x={x}:y={y}:enable='between(t,{ini:.3f},{fim:.3f})'")

    # ── legenda e textos (libass) ──
    if tem_ass:
        v = g.add(v, "subtitles=filename=legenda.ass:fontsdir=fontes")

    # ── transicao ──
    tr = _achar(passos, "transicao")
    if tr:
        e_, s_ = min(tr["entrada"], dur / 3), min(tr["saida"], dur / 3)
        fx = []
        if e_ > 0:
            fx.append(f"fade=t=in:st=0:d={e_:.2f}")
        if s_ > 0:
            fx.append(f"fade=t=out:st={max(0.0, dur - s_):.3f}:d={s_:.2f}")
        if fx:
            v = g.add(v, ",".join(fx))
    v = g.add(v, "format=yuv420p", "vout")

    # ── som ──
    if a is None:
        a = g.add([], f"anullsrc=r=48000:cl=stereo,atrim=0:{dur:.3f}")
    som = _achar(passos, "som")
    voz_viva = tem_audio
    if som:
        if som["acao"] == "mudo":
            a = g.add(a, "volume=0")
            voz_viva = False
        elif som["acao"] in ("aumentar", "diminuir"):
            a = g.add(a, f"volume={float(som['fator']):.2f},alimiter=limit=0.97")
        elif som["acao"] == "normalizar":
            a = g.add(a, "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000")
    mus = _achar(passos, "musica")
    if mus:
        entradas += ["-stream_loop", "-1", "-i", mus["arquivo"]]
        idx = n_in
        n_in += 1
        vol = float(mus["volume"])
        if not voz_viva and vol < 0.3:
            vol = 0.6
        caida = min(2.0, dur / 4)
        mm = g.add(f"{idx}:a", (f"aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                                f"atrim=0:{dur:.3f},asetpts=PTS-STARTPTS,volume={vol:.3f},"
                                f"afade=t=out:st={max(0.0, dur - caida):.3f}:d={caida:.2f}"))
        if voz_viva:
            a1, a2 = g.rotulo("vz"), g.rotulo("vz")
            g.linhas.append(f"[{a}]asplit=2[{a1}][{a2}]")
            md = g.add([mm, a2], "sidechaincompress=threshold=0.03:ratio=6:attack=15:release=400")
            a = g.add([a1, md], "amix=inputs=2:duration=first:dropout_transition=0:normalize=0")
        else:
            a = g.add([a, mm], "amix=inputs=2:duration=first:dropout_transition=0:normalize=0")
    if tr:
        fx = []
        if tr["entrada"] > 0:
            fx.append(f"afade=t=in:st=0:d={min(tr['entrada'], dur / 3):.2f}")
        if tr["saida"] > 0:
            s_ = min(tr["saida"], dur / 3)
            fx.append(f"afade=t=out:st={max(0.0, dur - s_):.3f}:d={s_:.2f}")
        if fx:
            a = g.add(a, ",".join(fx))
    g.add(a, "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo", "aout")
    return {"entradas": entradas, "grafo": g.texto(), "dur": dur, "W": W, "H": H,
            "avisos": avisos, "leve": leve, "alta": bool(qual and qual["nivel"] == "alta"),
            "ajustes_ass": ajustes_ass}


# ═════════════════════════════════════════════════════════════════════════════
#  O TRABALHO EM SEGUNDO PLANO
# ═════════════════════════════════════════════════════════════════════════════

class _Cancelado(Exception):
    pass


# So para teste: guarda a pasta de trabalho (grafo, legenda) em vez de apagar,
# e nao abre o video pronto na tela de quem esta rodando o teste.
_MANTER_TEMP = False
_ABRIR_NO_FIM = True


_TRAVA = threading.Lock()
_JOB: dict = {}
_ULTIMA: dict = {}


def _anotar(evento: str, detalhe: str = "") -> None:
    """Uma linha em logs/voice_session.log — o JARVIS roda no pythonw e o
    `print` nao aparece em lugar nenhum."""
    try:
        from datetime import datetime as _dt  # noqa: PLC0415
        caminho = _RAIZ / "logs" / "voice_session.log"
        caminho.parent.mkdir(parents=True, exist_ok=True)
        with open(caminho, "a", encoding="utf-8") as f:
            f.write(f"{_dt.now().isoformat(timespec='milliseconds')}\t{evento}\t{detalhe}\n")
    except Exception:
        pass


_CHAVE_NO_REGISTRO = {"area_de_trabalho": "Desktop", "videos": "My Video",
                      "documentos": "Personal",
                      "downloads": "{374DE290-123F-4565-9164-39C4925E467B}"}
_CHAVE_NA_PLATAFORMA = {"area_de_trabalho": "desktop", "videos": "videos",
                        "documentos": "documents", "downloads": "downloads"}


def _pasta_do_sistema(chave: str) -> Path | None:
    """A pasta de verdade ('area_de_trabalho', 'downloads'...). No Windows ela
    sai do registro: a area de trabalho de muita gente mora no OneDrive, e
    `~/Desktop` ai existe mas nao e a que aparece na tela."""
    if os.name == "nt" and chave in _CHAVE_NO_REGISTRO:
        try:
            import winreg  # noqa: PLC0415
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows"
                                r"\CurrentVersion\Explorer\User Shell Folders") as k:
                bruto, _ = winreg.QueryValueEx(k, _CHAVE_NO_REGISTRO[chave])
            p = Path(os.path.expandvars(str(bruto)))
            if p.is_dir():
                return p
        except Exception:
            pass
    try:
        from core.plataforma import pasta_conhecida  # noqa: PLC0415
        p = pasta_conhecida(_CHAVE_NA_PLATAFORMA.get(chave, ""))
        if p:
            return Path(p)
    except Exception:
        pass
    return None


_NOME_DA_PASTA = {"area_de_trabalho": "na sua area de trabalho", "downloads": "nos seus Downloads",
                  "videos": "na pasta Videos", "documentos": "nos seus Documentos"}


def _onde_ficou(destino: Path, video: str, pedida: str = "") -> str:
    """"na sua area de trabalho" / "na mesma pasta do original" / ...

    A pasta que ele PEDIU e dita pelo nome dele, mesmo quando o original ja
    estava la: pediu "na area de trabalho", ouve "na sua area de trabalho".
    """
    pasta = os.path.normcase(str(destino.parent))
    chaves = ([pedida] if pedida in _NOME_DA_PASTA else []) + [
        "mesma_pasta", "area_de_trabalho", "downloads", "videos", "documentos"]
    for chave in chaves:
        if chave == "mesma_pasta":
            if pasta == os.path.normcase(str(Path(video).parent)):
                # "Na mesma pasta do original" obriga a lembrar onde era; se
                # e uma pasta conhecida, diz o nome dela.
                for k, nome in _NOME_DA_PASTA.items():
                    p = _pasta_do_sistema(k)
                    if p and os.path.normcase(str(p)) == pasta:
                        return f"{nome}, junto do original"
                return "na mesma pasta do original"
            continue
        p = _pasta_do_sistema(chave)
        if p and os.path.normcase(str(p)) == pasta:
            return _NOME_DA_PASTA[chave]
    return f"na pasta {destino.parent}"


def _nome_de_saida(video: str, onde: str = "") -> Path:
    origem = Path(video)
    base = re.sub(r"\s*\(editado(?: \d+)?\)$", "", origem.stem).strip() or "video"
    pastas = [origem.parent]
    if onde:
        # Ele disse onde quer ("salva na area de trabalho"): essa vem primeiro.
        pedida = _pasta_do_sistema(onde)
        if pedida:
            pastas.insert(0, pedida)
    try:
        from core.plataforma import pasta_conhecida  # noqa: PLC0415
        vids = pasta_conhecida("videos")
        if vids:
            pastas.append(vids)
    except Exception:
        pastas.append(Path.home() / "Videos")
    for pasta in pastas:
        try:
            teste = pasta / ".jarvis_teste_escrita"
            teste.write_bytes(b"")
            teste.unlink()
        except Exception:
            continue
        for n in range(1, 100):
            nome = f"{base} (editado).mp4" if n == 1 else f"{base} (editado {n}).mp4"
            alvo = pasta / nome
            if not alvo.exists():
                return alvo
    return Path(tempfile.gettempdir()) / f"{base} (editado).mp4"


def _estimativa(ficha: dict, passos: list, dur_saida: float, codificador: str) -> str:
    """Tempo que o dono vai esperar, em palavras. Palpite honesto: 'uns'."""
    if _achar(passos, "formato"):
        W, H = _PROPORCOES[_achar(passos, "formato")["proporcao"]]
    else:
        W, H = ficha["largura"], ficha["altura"]
    px = (W * H) / (1920 * 1080)
    fps = min(60.0, float(ficha.get("fps") or 30))
    ritmo = 110.0 if codificador == "h264_nvenc" else 45.0      # quadros/s em 1080p
    seg = dur_saida * fps * px / ritmo
    if _achar(passos, "formato") and _achar(passos, "formato")["modo"] == "desfocado":
        seg *= 1.2
    seg += ficha["duracao"] * 0.02                                # ler o som
    if _achar(passos, "cortar_silencio"):
        # Cada corte e um ramo do grafo por onde TODO quadro passa: 430 cortes
        # num video de 10 min custaram ~100 s so disso (medido em 10/09/2026).
        seg += ficha["duracao"] * 0.15
    if _achar(passos, "legenda"):
        seg += ficha["duracao"] * 0.3
    if _achar(passos, "juntar"):
        seg *= 1.8
    if seg < 50:
        return "menos de um minuto"
    minutos = int(round(seg / 60.0)) or 1
    return "uns 2 minutos" if minutos == 2 else f"uns {minutos} minutos" if minutos > 1 else "cerca de um minuto"


def _rodar_ffmpeg(cmd: list, dur: float, job: dict, cwd: str, env: dict, fase: str,
                  log) -> tuple:
    """Roda o ffmpeg lendo o andamento. (codigo de saida, fim do stderr)."""
    proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                            creationflags=_SEM_JANELA | _PRIORIDADE_BAIXA)
    job["proc"] = proc
    cauda: list = []

    def _ler_erro():
        for linha in iter(proc.stderr.readline, b""):
            cauda.append(linha.decode("utf-8", "replace").rstrip())
            del cauda[:-40]

    threading.Thread(target=_ler_erro, daemon=True).start()
    # `t` = quando o relogio do video andou pela ultima vez. E o relogio que
    # conta, nao a porcentagem: num video de duas horas num PC fraco, 1% pode
    # levar mais que os 4 minutos do vigia.
    ultimo = {"t": time.monotonic(), "pct": -1, "pos": -1.0}

    def _vigia():
        # Sem andamento por 4 minutos: travou. Mata para nao ficar pendurado.
        while proc.poll() is None:
            time.sleep(2)
            if job.get("cancelar"):
                try:
                    proc.kill()
                except Exception:
                    pass
                return
            if time.monotonic() - ultimo["t"] > 240:
                cauda.append("[vigia] quatro minutos sem andamento; parei")
                try:
                    proc.kill()
                except Exception:
                    pass
                return

    threading.Thread(target=_vigia, daemon=True).start()
    avisado = -10
    for linha in iter(proc.stdout.readline, b""):
        s = linha.decode("utf-8", "replace").strip()
        if s.startswith("out_time_us=") or s.startswith("out_time_ms="):
            try:
                t = int(s.split("=", 1)[1]) / 1e6
            except Exception:
                continue
            if t > ultimo["pos"]:
                ultimo["pos"] = t
                ultimo["t"] = time.monotonic()
            pct = int(max(0.0, min(99.0, 100.0 * t / max(dur, 0.1))))
            if pct != ultimo["pct"]:
                ultimo["pct"] = pct
                job["pct"] = pct
                if pct - avisado >= 20:
                    avisado = pct
                    log(f"[editar_video] {fase}: {pct}%")
    rc = proc.wait()
    job["proc"] = None
    return rc, "\n".join(cauda[-15:])


def _parametros_de_codificacao(cod: str, leve: bool, alta: bool) -> list:
    crf = 28 if leve else 18 if alta else 21
    if cod == "h264_nvenc":
        cv = ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", str(crf + 2), "-b:v", "0"]
    else:
        cv = ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf)]
    return cv + ["-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k" if leve else "160k",
                 "-ar", "48000", "-movflags", "+faststart"]


def _juntar(ff: str, principal: str, ficha: dict, outros: list, antes: bool, tmp: Path,
            job: dict, env: dict, log) -> str:
    """Emenda os videos num so, todos no tamanho e no fps do principal."""
    videos = (outros + [principal]) if antes else ([principal] + outros)
    W, H = _par(ficha["largura"]), _par(ficha["altura"])
    if max(W, H) > 1920:
        esc = 1920 / max(W, H)
        W, H = _par(W * esc), _par(H * esc)
    fps = _fps_de_saida(float(ficha.get("fps") or 30))
    g = _Grafo()
    entradas, pares, total = [], [], 0.0
    for i, vid in enumerate(videos):
        fi = ficha if vid == principal else ficha_do_video(vid)
        if not fi.get("ok"):
            raise RuntimeError(f"nao consegui abrir o video {Path(vid).name}")
        entradas += ["-i", vid]
        pares.append(g.add(f"{i}:v", (f"fps={fps},scale={W}:{H}:force_original_aspect_ratio=decrease:"
                                      f"force_divisible_by=2,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,"
                                      f"setsar=1,format=yuv420p")))
        if fi.get("tem_audio"):
            pares.append(g.add(f"{i}:a", "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"))
        else:
            pares.append(g.add([], f"anullsrc=r=48000:cl=stereo,atrim=0:{fi['duracao']:.3f}"))
        total += fi["duracao"]
    g.linhas.append("".join(f"[{p}]" for p in pares) + f"concat=n={len(videos)}:v=1:a=1[jv][ja]")
    (tmp / "juntar.txt").write_text(g.texto(), encoding="utf-8")
    saida = tmp / "juntado.mp4"
    cmd = ([ff, "-hide_banner", "-y", "-nostdin", "-loglevel", "error", "-progress", "pipe:1",
            "-nostats"] + entradas + _script_de_grafo("juntar.txt")
           + ["-map", "[jv]", "-map", "[ja]", "-c:v", "libx264", "-preset", "ultrafast",
              "-crf", "16", "-c:a", "aac", "-b:a", "192k", str(saida)])
    rc, erro = _rodar_ffmpeg(cmd, total, job, str(tmp), env, "juntando", log)
    if job.get("cancelar"):
        raise _Cancelado()
    if rc != 0 or not saida.is_file():
        raise RuntimeError("nao consegui juntar os videos: " + erro[-200:])
    return str(saida)


def _script_de_grafo(nome: str) -> list:
    # O ffmpeg 7 trocou a opcao; o 6.1 do instalador ainda usa a antiga.
    if _FFMPEG_VERSAO >= 7:
        return ["-/filter_complex", nome]
    return ["-filter_complex_script", nome]


def _trabalho(job: dict, falar, player, ouvido, log) -> None:
    ff = achar_ffmpeg()
    tmp = Path(tempfile.mkdtemp(prefix="jarvis_video_"))
    passos = job["passos"]
    video = job["video"]
    ficha = job["ficha"]
    cancelado = lambda: bool(job.get("cancelar"))  # noqa: E731
    env = dict(os.environ)
    t0 = time.monotonic()
    try:
        # A libass acha fonte pelo fontconfig. Sem um arquivo de configuracao
        # proprio, a primeira legenda do PC pode levar MINUTOS montando o cache
        # das fontes do Windows inteiro. Aqui ele so conhece a pasta `fontes`.
        fontes = tmp / "fontes"
        grossa, normal = _fontes(fontes)
        (tmp / "fonts.conf").write_text(
            '<?xml version="1.0"?>\n<fontconfig>\n'
            f'  <dir>{fontes.as_posix()}</dir>\n  <cachedir>{(tmp / "fc").as_posix()}</cachedir>\n'
            '</fontconfig>\n', encoding="utf-8")
        env["FONTCONFIG_FILE"] = str(tmp / "fonts.conf")

        entrada = video
        jn = _achar(passos, "juntar")
        if jn:
            job["fase"] = "juntando os videos"
            entrada = _juntar(ff, video, ficha, jn["arquivos"], jn["posicao"] == "antes",
                              tmp, job, env, log)
            ficha = ficha_do_video(entrada)
            if not ficha.get("ok"):
                raise RuntimeError("o video juntado saiu ilegivel")
        dur_in = float(ficha["duracao"])

        # ── linha do tempo ──
        segs = [(0.0, dur_in)]
        mantem = [(p["inicio"], p["fim"]) for p in passos if p["tipo"] == "manter_trecho"]
        if mantem:
            segs = _intersecao(segs, _uniao(mantem))
        for p in passos:
            if p["tipo"] == "remover_trecho":
                segs = _subtrair(segs, p["inicio"], p["fim"])
        cs = _achar(passos, "cortar_silencio")
        if cs:
            job["fase"] = "procurando os silencios"
            log("[editar_video] procurando os silencios...")
            if not ficha.get("tem_audio"):
                job["avisos"].append("o video nao tem som, entao nao havia silencio para cortar")
            else:
                db = _envelope_db(ff, entrada, cancelado)
                if cancelado():
                    raise _Cancelado()
                fala, motivo = _trechos_com_fala(db, dur_in, float(cs["silencio_minimo"]))
                if fala is None:
                    job["avisos"].append(motivo)
                else:
                    novo = _intersecao(segs, fala)
                    guardado = sum(b - a for a, b in novo)
                    antes = sum(b - a for a, b in segs)
                    if guardado < max(1.5, 0.08 * antes):
                        job["avisos"].append("quase todo o video parecia silencio; preferi nao cortar")
                    else:
                        log(f"[editar_video] silencio: {motivo}; {len(novo)} trechos, "
                            f"{antes - guardado:.1f} s cortados")
                        job["cortado"] = antes - guardado
                        segs = novo
        segs = [(a, b) for a, b in segs if b - a >= 0.08]
        if not segs:
            raise RuntimeError("os cortes pedidos nao deixaram nada do video")

        # ── legenda ──
        vel = _achar(passos, "velocidade")
        fator = float(vel["fator"]) if vel else 1.0
        dur_saida = sum(b - a for a, b in segs) / fator
        palavras = []
        leg = _achar(passos, "legenda")
        if leg:
            if not ficha.get("tem_audio"):
                job["avisos"].append("o video nao tem som, entao nao ha o que legendar")
            elif _achar(passos, "ao_contrario"):
                job["avisos"].append("legenda em video de tras para frente nao faz sentido; pulei")
            else:
                job["fase"] = "escrevendo a legenda"
                log("[editar_video] ouvindo o video para escrever a legenda...")
                try:
                    brutas = _transcrever(ff, entrada, ouvido, leg["idioma"], segs[0][0],
                                          segs[-1][1], cancelado, log)
                    palavras = _mapear_palavras(brutas, segs, fator)
                    if not palavras:
                        job["avisos"].append("nao entendi fala nenhuma para a legenda")
                except Exception as exc:
                    if cancelado():
                        raise _Cancelado()
                    log(f"[editar_video] legenda falhou: {type(exc).__name__}: {str(exc)[:140]}")
                    job["avisos"].append("a legenda nao saiu desta vez")
        if cancelado():
            raise _Cancelado()
        textos = [p for p in passos if p["tipo"] == "texto"]

        # ── montar e renderizar ──
        disp = _filtros_disponiveis(ff)
        tem_ass = bool(palavras or textos)
        receita = _montar(passos, ficha, segs, tem_ass, entrada, disp)
        job["avisos"] += receita["avisos"]
        if tem_ass:
            (tmp / "legenda.ass").write_text(
                _montar_ass(receita["W"], receita["H"], receita["dur"], palavras,
                            (leg or {}).get("estilo", "tiktok"), textos, grossa, normal,
                            receita.get("ajustes_ass")),
                encoding="utf-8")
        (tmp / "grafo.txt").write_text(receita["grafo"], encoding="utf-8")
        destino = _nome_de_saida(video, (_achar(passos, "salvar") or {}).get("onde", ""))
        parcial = destino.with_name(destino.stem + ".parcial.mp4")
        job["parcial"] = str(parcial)
        cod = _codificador(ff)
        base_cmd = ([ff, "-hide_banner", "-y", "-nostdin", "-loglevel", "error",
                     "-progress", "pipe:1", "-nostats"] + receita["entradas"]
                    + _script_de_grafo("grafo.txt") + ["-map", "[vout]", "-map", "[aout]"])
        job["fase"] = "montando o video"
        job["pct"] = 0
        log(f"[editar_video] montando o video ({receita['W']}x{receita['H']}, "
            f"{receita['dur']:.1f} s, {cod})...")
        cmd = base_cmd + _parametros_de_codificacao(cod, receita["leve"], receita["alta"]) \
            + ["-t", f"{receita['dur'] + 0.5:.3f}", "-f", "mp4", str(parcial)]
        rc, erro = _rodar_ffmpeg(cmd, receita["dur"], job, str(tmp), env, "montando", log)
        if cancelado():
            raise _Cancelado()
        if rc != 0 and cod != "libx264":
            # A placa recusou no meio: refaz no processador, que sempre funciona.
            log(f"[editar_video] a placa falhou ({erro[-120:]}); refazendo no processador")
            cmd = base_cmd + _parametros_de_codificacao("libx264", receita["leve"], receita["alta"]) \
                + ["-t", f"{receita['dur'] + 0.5:.3f}", "-f", "mp4", str(parcial)]
            rc, erro = _rodar_ffmpeg(cmd, receita["dur"], job, str(tmp), env, "montando", log)
        if rc != 0 and any(p["tipo"] == "filtro_livre" for p in passos) and not cancelado():
            log(f"[editar_video] o efeito especial quebrou o video ({erro[-120:]}); tirando ele")
            job["avisos"].append("o efeito especial nao funcionou e ficou de fora")
            passos2 = [p for p in passos if p["tipo"] != "filtro_livre"]
            receita = _montar(passos2, ficha, segs, tem_ass, entrada, disp)
            (tmp / "grafo.txt").write_text(receita["grafo"], encoding="utf-8")
            cmd = ([ff, "-hide_banner", "-y", "-nostdin", "-loglevel", "error", "-progress",
                    "pipe:1", "-nostats"] + receita["entradas"] + _script_de_grafo("grafo.txt")
                   + ["-map", "[vout]", "-map", "[aout]"]
                   + _parametros_de_codificacao("libx264", receita["leve"], receita["alta"])
                   + ["-t", f"{receita['dur'] + 0.5:.3f}", "-f", "mp4", str(parcial)])
            rc, erro = _rodar_ffmpeg(cmd, receita["dur"], job, str(tmp), env, "montando", log)
        if cancelado():
            raise _Cancelado()
        if rc != 0 or not parcial.is_file() or parcial.stat().st_size < 1000:
            _anotar("editar_video_falhou", erro.replace("\n", " | ")[-600:])
            log(f"[editar_video] ffmpeg: {erro[-700:]}")
            raise RuntimeError("o ffmpeg recusou a montagem")

        # ── conferencia: o video pronto tem a duracao que devia ter ──
        pronto = ficha_do_video(str(parcial))
        if not pronto.get("ok") or pronto["duracao"] < receita["dur"] * 0.9 - 0.5:
            _anotar("editar_video_curto", f"esperado {receita['dur']:.1f}s, saiu "
                                          f"{pronto.get('duracao', 0):.1f}s")
            raise RuntimeError("o video saiu mais curto do que devia")
        os.replace(parcial, destino)
        job["saida"] = str(destino)
        gasto = time.monotonic() - t0
        _anotar("editar_video_ok", f"{Path(video).name} -> {destino.name} | {receita['dur']:.1f}s "
                                   f"| {gasto:.0f}s de trabalho | {cod}")
        with _TRAVA:
            _ULTIMA.clear()
            _ULTIMA.update({"original": video, "saida": str(destino),
                            "passos": job["passos_brutos"], "quando": time.time()})
        abriu = False
        if _ABRIR_NO_FIM:
            try:
                from core.plataforma import abrir_no_sistema  # noqa: PLC0415
                abriu = abrir_no_sistema(str(destino))
            except Exception:
                try:
                    os.startfile(str(destino))  # noqa: S606
                    abriu = True
                except Exception:
                    pass
        frase = (f"Pronto, chefe! O video ficou pronto{' e ja abri pra voce' if abriu else ''}. "
                 f"Salvei como {destino.name}, "
                 f"{_onde_ficou(destino, video, (_achar(passos, 'salvar') or {}).get('onde', ''))}.")
        if job.get("cortado", 0) > 1:
            frase += f" Cortei {int(round(job['cortado']))} segundos de silencio."
        avisos = [x for x in dict.fromkeys(job["avisos"])
                  if x and x not in job.get("avisos_ditos", set())]
        if avisos:
            frase += " So um detalhe: " + "; ".join(avisos[:2]) + "."
        log(f"SIS: video editado salvo em {destino}")
        try:
            from memory.diario import anotar as _diario  # noqa: PLC0415
            _diario("fez", f"editar_video {Path(video).name}", f"pronto: {destino}")
        except Exception:
            pass
        _falar(falar, frase)
    except _Cancelado:
        log("SIS: parei a edicao do video.")
        _falar(falar, "Parei a edicao do video, chefe. O original continua intacto.")
    except Exception as exc:
        _anotar("editar_video_erro", f"{type(exc).__name__}: {str(exc)[:300]}")
        log(f"[editar_video] falhou: {type(exc).__name__}: {str(exc)[:200]}")
        motivo = str(exc) if isinstance(exc, RuntimeError) else "deu um erro no meio"
        _falar(falar, f"Nao consegui terminar a edicao do video: {motivo[:120]}. "
                      f"O original continua intacto.")
    finally:
        # O pedaco que nao virou video pronto (falha ou "para") nao fica no
        # disco do dono. So o NOSSO arquivo parcial, pelo caminho exato.
        try:
            resto = Path(job.get("parcial") or "")
            if job.get("parcial") and resto.is_file():
                resto.unlink()
        except Exception:
            pass
        if _MANTER_TEMP:
            log(f"[editar_video] pasta de trabalho guardada: {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)
        with _TRAVA:
            job["vivo"] = False


def _falar(falar, frase: str) -> None:
    if falar is None:
        return
    try:
        falar(frase)
    except Exception:
        pass


# ═════════════════════════════════════════════════════════════════════════════
#  A FERRAMENTA
# ═════════════════════════════════════════════════════════════════════════════

def _em_andamento() -> dict | None:
    with _TRAVA:
        return _JOB if _JOB.get("vivo") else None


def editar_video(parameters: dict | None = None, player=None, gerar=None, falar=None,
                 ouvido=None, ver=None, **_ignorado) -> str:
    """Ferramenta: edita o video que o dono pediu, aqui no PC.

    `pedido`  o que fazer, com as palavras do dono
    `video`   caminho ou nome do video (vazio = o arrastado para a janela do
              JARVIS, ou o selecionado no Explorer)
    `acao`    editar (padrao) | status | cancelar
    """
    p = parameters or {}

    def log(msg: str) -> None:
        if player is not None and hasattr(player, "write_log"):
            try:
                player.write_log(msg)
            except Exception:
                pass

    acao = _sem_acento(p.get("acao") or "editar")
    atual = _em_andamento()
    if any(k in acao for k in ("status", "andamento", "progresso")):
        if not atual:
            if _ULTIMA.get("saida"):
                return f"Nao estou editando nada agora. O ultimo video ficou pronto: {Path(_ULTIMA['saida']).name}."
            return "Nao estou editando nenhum video agora."
        pct = f", {atual.get('pct', 0)}% pronto" if atual.get("fase") == "montando o video" else ""
        return f"Estou editando o {Path(atual['video']).name}: {atual.get('fase', 'trabalhando')}{pct}."
    if any(k in acao for k in ("cancel", "parar", "para")):
        if not atual:
            return "Nao ha nenhuma edicao de video rodando."
        atual["cancelar"] = True
        try:
            if atual.get("proc"):
                atual["proc"].kill()
        except Exception:
            pass
        return "Certo, estou parando a edicao do video."
    if atual:
        # Sem fila de proposito: prometer "depois eu faco" e esquecer e pior
        # do que pedir para repetir.
        return (f"Ainda estou editando o {Path(atual['video']).name} "
                f"({atual.get('fase', 'trabalhando')}). Edito um video de cada vez: "
                f"quando esse ficar pronto, me pede de novo.")

    ff = achar_ffmpeg()
    if not ff:
        return "Nao achei o editor de video (ffmpeg) aqui no computador; ele devia ter vindo no instalador."
    video = _video_do_pedido(p, player)
    if not video:
        return ("Qual video eu edito? Arrasta ele para a minha janela, ou seleciona o arquivo "
                "na pasta e me pede de novo.")
    ficha = ficha_do_video(video)
    if not ficha.get("ok"):
        return f"Nao consegui abrir o video {Path(video).name}: {ficha.get('erro', 'formato estranho')}."
    pedido = str(p.get("pedido") or p.get("description") or p.get("instrucao") or "").strip()
    fala = str(p.get("_fala_do_dono") or "").strip()
    if fala and fala.lower() not in pedido.lower():
        pedido = (pedido + " | frase do cliente: " + fala).strip(" |")
    if not pedido:
        return ("O que eu faco no video? Por exemplo: deixar no formato do TikTok, cortar os "
                "silencios, colocar legenda ou musica.")
    with _TRAVA:
        anterior = dict(_ULTIMA) if (_ULTIMA.get("original") == video
                                     and time.time() - _ULTIMA.get("quando", 0) < 3600) else None
    # As imagens que o dono arrastou junto com o video (a janela so guarda um
    # arquivo; quem lembra de todas e a rede do main.py). `imagem_arrastada`
    # e a da janela quando ele disse "coloca essa imagem no video".
    imagem = str(p.get("imagem_arrastada") or "").strip()
    brutas = p.get("imagens_arrastadas") or []
    if isinstance(brutas, str):
        brutas = [brutas]
    imagens: list = []
    for c in list(brutas) + [imagem]:
        c = str(c or "").strip()
        if (c and Path(c).is_file() and Path(c).suffix.lower() in EXT_IMAGEM
                and os.path.normcase(c) not in {os.path.normcase(x) for x in imagens}):
            imagens.append(c)
    if imagem not in imagens:
        imagem = ""
    if not imagem and not _FALA_DE_IMAGEM.search(" ".join(_palavras(pedido))):
        # Arrastou imagens mas o pedido nao fala delas ("corta os silencios"):
        # nem olhar nem oferecer ao cerebro, que poderia inventar de por.
        imagens = []
    if isinstance(p.get("_passos"), list):
        # Receita pronta (os testes usam): pula o cerebro.
        brutos, nao_da, fonte = p["_passos"], "", "receita pronta"
    else:
        fichas_img = [{"arquivo": c, "forma": _forma_da_imagem(ff, c)} for c in imagens]
        if len(imagens) >= 2:
            # Duas ou mais: o dono diz qual vai onde pelo que TEM nelas ("a do
            # site em cima") e o nome do arquivo nao ajuda ("ChatGPT Image
            # (1).png"). Alguem tem que olhar.
            t_olhar = time.monotonic()
            for f, d in zip(fichas_img, _descrever_imagens(ff, imagens, ver, log)):
                f["descricao"] = d
            _anotar("editar_video_olhou", f"{time.monotonic() - t_olhar:.1f}s | " + " || ".join(
                f"{Path(f['arquivo']).name}: {f.get('descricao') or '-'}" for f in fichas_img)[:600])
        brutos, nao_da, fonte = _planejar(pedido, ficha, Path(video).name, gerar, anterior, log,
                                          fichas_img)
    passos, avisos = _limpar_plano(brutos, ficha, video, imagens)
    _garantir_imagens(passos, pedido, imagens, imagem, bool(_achar(passos, "formato")))
    log(f"[editar_video] plano ({fonte}): {json.dumps(passos, ensure_ascii=False)[:600]}")
    _anotar("editar_video_plano", f"{fonte} | {Path(video).name} | {json.dumps(passos, ensure_ascii=False)[:500]}")
    if not [q for q in passos if q["tipo"] != "salvar"]:
        passos = []
    if not passos:
        extra = f" Isso eu ainda nao faco: {nao_da}." if nao_da else ""
        return ("Nao entendi o que mudar no video." + extra +
                " Posso cortar silencio, mudar o formato, espelhar, por legenda, texto, imagem, musica e efeitos.")
    # O cerebro as vezes poe em `nao_da` o arquivo que faltou ("nome do arquivo
    # da musica"). Isso ja vira aviso proprio, e dito como "isso eu nao faco"
    # soaria falso: colocar musica eu faco, so faltou saber qual.
    # FULL HD EU FACO (10/09/2026): o cerebro pos "Full HD especifico" em
    # `nao_da` e o Diego ouviu duas vezes "isso eu ainda nao faco" de um video
    # que saiu em 1080x1920. Resolucao ate 1080p e o padrao da casa; so 4K
    # para cima e que nao sai.
    nd = _sem_acento(nao_da)
    if nao_da and re.search(r"full ?hd|\bhd\b|1080|resoluc|qualidade|nitid|definic", nd) \
            and not re.search(r"\b(4 ?k|2160|8 ?k|1440|2 ?k)\b", nd):
        nao_da = ""
        if not _achar(passos, "qualidade"):
            passos.append({"tipo": "qualidade", "nivel": "alta"})
    if nao_da and not re.search(r"arquivo|nome|caminho|qual", nd):
        avisos.append(f"isso eu ainda nao faco: {nao_da[:80]}")

    # duracao de saida estimada so para o aviso de tempo
    dur_est = ficha["duracao"]
    for q in passos:
        if q["tipo"] == "manter_trecho":
            dur_est = min(dur_est, q["fim"] - q["inicio"])
    if _achar(passos, "cortar_silencio"):
        dur_est *= 0.8
    if _achar(passos, "velocidade"):
        dur_est /= float(_achar(passos, "velocidade")["fator"])
    espera = _estimativa(ficha, passos, dur_est, _codificador(ff))

    job = {"vivo": True, "video": video, "ficha": ficha, "passos": passos,
           "passos_brutos": [q for q in passos], "avisos": list(avisos),
           # Os que ja saem na frase de agora nao se repetem no "Pronto".
           "avisos_ditos": set(avisos[:2]),
           "fase": "comecando", "pct": 0, "cancelar": False, "proc": None,
           "inicio": time.time()}
    with _TRAVA:
        _JOB.clear()
        _JOB.update(job)
        job = _JOB
    threading.Thread(target=_trabalho, args=(job, falar, player, ouvido, log),
                     daemon=True, name="editar_video").start()
    resumo = _resumo(passos)
    # "AINDA NAO ESTA PRONTO" dito com todas as letras: no teste com o cerebro
    # de verdade ele recebeu "comecei" e contou ao dono "o video ficou pronto".
    frase = (f"Comecei a editar o video, ainda nao esta pronto: vou {resumo}. "
             f"Leva {espera}, e eu aviso quando terminar.")
    if avisos:
        frase += " Obs.: " + "; ".join(avisos[:2]) + "."
    return frase
