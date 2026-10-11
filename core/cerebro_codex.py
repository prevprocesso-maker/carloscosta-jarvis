# -*- coding: utf-8 -*-
"""O CEREBRO DO JARVIS: o Codex CLI da VPS (04/09/2026).

Por que ele existe
------------------
Decisao do Diego, depois de medirmos os tres caminhos com o pedido de verdade:

    Gemini gratuita do cliente ..... 0,7 a 1,0 s   ~20 perguntas/dia, 429 direto
    OpenRouter gratuito do cliente . 1,1 a 1,9 s   50 pedidos/dia, as vezes vazio
    Codex CLI na VPS ............... 4,5 a 5,4 s   SEM cota, no plano do dono

Ele escolheu o Codex e mandou tirar o OpenRouter inteiro: "assim nao dependo
mais de cota de ninguem". O preco combinado sao os ~5 s por resposta; o ganho e
que o JARVIS nunca mais fica sem responder no meio do dia do cliente.

O DESENHO E O MESMO DO COMPUTER USE, e nao por acaso — aquele ja esta aprovado
e rodando: a VPS DECIDE, o PC EXECUTA. O Codex nunca toca no computador do
cliente; ele recebe a fala e a lista de ferramentas do JARVIS e devolve uma linha
de JSON:

    {"fala": "..."}                                    so conversa
    {"fala": "...", "ferramenta": "nome", "args": {}}   chamar ferramenta

Quem executa a ferramenta e o `executor` do proprio JARVIS — o mesmo que a Gemini
usa. Depois de executar, o resultado volta para a VPS e ele fecha com a frase
final, contando o que aconteceu de verdade.

O contrato desta classe e IGUAL ao do `CerebroGemini.responder`, de proposito:
assim o `cerebro_openrouter` (que orquestra prompt, historico e travas) troca um
pelo outro sem saber a diferenca.
"""
from __future__ import annotations

import json
import re
import secrets
import threading


# DESCRICOES QUE MENTEM NESTE CANAL (05/09/2026).
#
# Algumas ferramentas foram escritas para o cerebro que RECEBE A IMAGEM (a
# Gemini ao vivo): "a foto e enviada diretamente para voce, descreva o que ve".
# O Codex nao recebe imagem por aqui — ele decide e o PC executa. Lendo aquilo,
# ele conclui, com razao, que nao consegue: na varredura de 05/09 ele respondeu
# "nao consigo capturar a tela porque essa ferramenta nao esta disponivel", com
# a ferramenta ali na lista.
#
# Entao a descricao E TROCADA so para este caminho. O texto original continua
# valendo para o caminho da Gemini, onde ele e verdade.
# ─────────────────────────────────────────────────────────────────────────────
# A MEMORIA CHEGA AO CEREBRO (05/09/2026)
# ─────────────────────────────────────────────────────────────────────────────
#
# Queixa do Diego: "ele tem que agir como uma IA avancada, entender o contexto
# da conversa". Fui ver e o defeito era estrutural, nao de comportamento:
#
#   * o JARVIS TEM memoria de longo prazo (`memory/long_term.json`) e TEM a
#     ferramenta de gravar nela (`save_memory`);
#   * mas a memoria so era montada dentro do `_build_config`, que e a
#     configuracao da GEMINI AO VIVO — um caminho que hoje nao decide nada.
#
# Ou seja: desde que o cerebro virou o Codex da VPS, ele nunca viu uma linha da
# memoria do dono. Perguntar "voce lembra do que eu te falei ontem?" nao tinha
# como funcionar.
#
# A memoria entra como NOTA ENTRE COLCHETES na propria pergunta. E o mesmo
# jeito que a hora ja usava neste projeto, e funciona por um motivo simples: a
# pergunta e o unico campo que a VPS repassa inteiro, sem cortar em 2.000
# letras como faz com o `jeito`.
_MEMORIA_MAX = 700
# ─────────────────────────────────────────────────────────────────────────────
# AS REGRAS QUE O CEREBRO PRECISA VER EM TODO TURNO (05/09/2026)
# ─────────────────────────────────────────────────────────────────────────────
#
# Elas estiveram por meia hora no `_persona_para_o_codex` — e NAO chegavam. Ali
# o texto e cortado em 1.800 letras (a VPS corta em 2.000), e a personalidade
# sozinha ja gastava quase tudo: da regra do "olhar nao e mexer" em diante,
# nada passava. Todo defeito que eu tentava consertar por prompt continuava
# acontecendo, e parecia teimosia do modelo.
#
# A pergunta e o unico campo que a VPS repassa inteiro (teto de 4.000). Entao e
# aqui que as regras moram, junto com a memoria e o diario.
#
# CADA LINHA DESTA LISTA NASCEU DE UM DEFEITO VISTO AO VIVO, e o defeito esta
# escrito ao lado para ninguem "limpar" a regra achando que e enfeite.
_REGRAS_DO_TURNO = (
    # Ele pediu "abre minha pasta de download" com a voz Orus e o ouvido
    # escreveu "Horus"; o modelo abriu uma pasta chamada Horus.
    "O texto que voce le foi OUVIDO por um microfone, nao digitado: pode vir "
    "palavra trocada ou pela metade. Entenda pelo som e pelo contexto e "
    "responda ao que ele quis dizer, sem comentar o erro. Se a primeira "
    "palavra parecer o seu proprio nome, e so o chamado: ignore e faca o que "
    "vem depois — nunca trate o seu nome como nome de pasta ou de programa.\n"
    # Ele falou da pasta de download e disse "abre ELA"; abriu o Explorador.
    "'ELA', 'ELE', 'ISSO' se referem ao que acabou de ser falado: troque o "
    "pronome pela coisa antes de escolher a ferramenta.\n"
    # "abre minha pasta de download" virou usar_computador, que e bloqueado.
    "Abrir pasta, programa ou site NAO e mexer no computador: use `open_app` "
    "ou `file_controller`, que nao precisam de ativacao nenhuma. "
    "`usar_computador` e so para clicar e digitar na tela, e a ordem de ATIVAR "
    "O COMPUTADOR nunca se deduz — so vale dita com todas as letras.\n"
    # "da uma olhadinha na pasta" fez ele ORGANIZAR a pasta.
    "OLHAR NAO E MEXER: ver, conferir e dar uma olhada e so olhar e contar. "
    "Organizar, apagar, mover ou renomear, so quando ele mandar.\n"
    # Ele escreveu no bloco de notas e depois disse "voce que digitou".
    "O seu DIARIO chega aqui em cima: cada linha marcada com VOCE FEZ e uma "
    "acao sua, com hora. E PROIBIDO dizer que nao sabe o que voce mesmo fez, e "
    "PROIBIDO jogar no dono uma coisa que foi voce quem fez.\n"
    # Uma tarefa falhou e ele cobrou o poema em toda resposta seguinte.
    "CADA PERGUNTA E NOVA: responda o que ele acabou de perguntar e so isso. O "
    "diario e para LEMBRAR quando ele perguntar; e proibido puxar de volta, "
    "por conta propria, assunto ou tarefa de antes.\n"
    # Ele contou coisas pessoais e nada era guardado.
    "Quando ele contar algo que vale para sempre — nome, cidade, trabalho, "
    "gosto, familia, projeto —, chame `save_memory` calado. Pedido do momento "
    "(hora, pesquisa, abrir programa) nao entra na memoria.\n"
    # Ele pediu para ver o objeto na mao e o modelo olhou a TELA, viu a
    # janelinha da webcam no canto e descreveu o que havia dentro dela.
    "CAMERA E TELA SAO COISAS DIFERENTES. O que ele esta segurando, o rosto "
    "dele, um gesto: e a WEBCAM. O que esta escrito ou aberto no computador: e "
    "a TELA. Se ele falar em webcam ou camera, use a ferramenta da camera — "
    "nunca a foto da tela.\n"
    # Ele perguntou "que objeto e esse na minha mao?" e o JARVIS respondeu com o
    # objeto de MEIA HORA ANTES, que estava no diario, sem olhar a camera.
    "O DIARIO E O PASSADO. Para saber o que esta acontecendo AGORA — o que a "
    "camera ve, o que esta na tela, que horas sao, o que tem numa pasta — OLHE "
    "de novo com a ferramenta, sempre. E proibido responder sobre o presente "
    "com o que esta escrito no diario.\n"
    # Ele perguntou "voce lembra o que escreveu no bloco de notas?" e o JARVIS
    # ABRIU o bloco de notas e digitou "Nao tenho acesso ao historico..." la
    # dentro. Duas vezes seguidas, com ele dizendo "nao quero que digite nada".
    "PERGUNTA NAO E ORDEM. 'Voce lembra...', 'o que voce escreveu', 'voce "
    "consegue...', 'o que voce acha' se respondem FALANDO, sem chamar "
    "ferramenta nenhuma. So mexa no computador quando ele MANDAR fazer algo. "
    "Na duvida entre responder e executar, responda falando.\n"
    # Saiu "let's get to work" no meio da frase.
    "Fale SEMPRE em portugues, nem uma expressao em ingles no meio da frase."
)


def _regras_do_turno() -> str:
    """As regras de cima na lingua do cliente (24/09/2026, ver core/idioma.py)."""
    try:
        from core.idioma import em_ingles, REGRA_INGLES  # noqa: PLC0415
        if em_ingles():
            return _REGRAS_DO_TURNO.replace(
                "Fale SEMPRE em portugues, nem uma expressao em ingles no meio da frase.",
                REGRA_INGLES)
    except Exception:
        pass
    return _REGRAS_DO_TURNO


def _fim_do_idioma() -> str:
    """Em ingles, a regra vai TAMBEM depois da pergunta: e o que o modelo le
    por ultimo, e o prompt da ponte na VPS pede portugues."""
    try:
        from core.idioma import em_ingles, REGRA_INGLES  # noqa: PLC0415
        return ("\n\n[" + REGRA_INGLES + "]") if em_ingles() else ""
    except Exception:
        return ""


def _precisa_traduzir(texto: str) -> bool:
    """Frase pronta de ferramenta, em portugues, com o cliente em ingles."""
    try:
        from core.idioma import em_ingles, parece_portugues  # noqa: PLC0415
        return em_ingles() and parece_portugues(texto)
    except Exception:
        return False



def _pastas_de_verdade() -> str:
    """Os caminhos REAIS desta maquina, para o modelo nao chutar.

    Defeito de 05/09/2026: pedindo para salvar na area de trabalho, ele montou
    `C:\\Users\\Edwin\\Desktop\\...` — inventou um nome de usuario que nao
    existe aqui, e o arquivo foi recusado duas vezes seguidas.
    """
    try:
        from pathlib import Path as _P  # noqa: PLC0415
        from actions.file_controller import (  # noqa: PLC0415
            _get_desktop, _get_downloads, _get_documents)
        return (f"area de trabalho={_get_desktop()} | downloads={_get_downloads()} "
                f"| documentos={_get_documents()} | casa={_P.home()}")
    except Exception:
        return ""


def _com_a_memoria(pergunta: str) -> str:
    """Junta na pergunta, como nota interna, o que o JARVIS ja sabe: a memoria
    do dono e os caminhos reais desta maquina."""
    partes = []
    try:
        from memory.memory_manager import load_memory, format_memory_for_prompt  # noqa: PLC0415
        texto = str(format_memory_for_prompt(load_memory()) or "").strip()
        if texto:
            if len(texto) > _MEMORIA_MAX:
                texto = texto[:_MEMORIA_MAX].rsplit("\n", 1)[0]
            partes.append(f"o que voce ja sabe sobre o dono, da memoria: {texto}")
    except Exception:
        pass
    # O DIARIO — o que foi dito e o que ELE fez, com hora. E o que responde
    # "voce lembra o que voce escreveu no bloco de notas?".
    try:
        from memory.diario import resumo_para_o_cerebro  # noqa: PLC0415
        # So a sessao atual; as antigas quando a FALA dele (a parte depois da
        # nota entre colchetes) pergunta do passado. Ver `memory/diario.py`.
        fala = str(pergunta or "").rsplit("]\n\n", 1)[-1]
        recente = str(resumo_para_o_cerebro(pergunta=fala) or "").strip()
        if recente:
            partes.append("o que aconteceu ha pouco (seu diario; o que esta "
                          "marcado como VOCE FEZ foi voce mesmo quem fez):\n"
                          + recente)
    except Exception:
        pass
    pastas = _pastas_de_verdade()
    if pastas:
        partes.append("as pastas DESTE computador, use exatamente estas e NUNCA "
                      "invente nome de usuario: " + pastas)
    if not partes:
        return "[" + _regras_do_turno() + "]\n\n" + pergunta + _fim_do_idioma()
    return ("[" + _regras_do_turno() + "]\n[" + "]\n[".join(partes)
            + "]\n\n" + pergunta + _fim_do_idioma())


def _resgatar_json(r: dict) -> dict:
    """A chamada da ferramenta escrita DENTRO da fala — resgatada (05/09/2026).

    O Diego pediu "da uma olhadinha na minha pasta de download" e o chat mostrou
    isto, letra por letra:

        Ah, claro... Vou dar uma olhada.{"ferramenta":"organizarpasta",
        "args":{...},"fala":"..."}{"faltam":true}

    Ou seja: o Codex escreveu a decisao como TEXTO em vez de devolver so o
    JSON, e o validador da VPS enfiou tudo dentro do campo `fala`. Duas coisas
    ruins de uma vez — a ferramenta NAO rodou, e as chaves foram parar no chat
    (e na boca, se a limpeza da voz nao pegasse).

    Aqui o bloco e lido de volta com o proprio leitor de JSON (`raw_decode`, que
    aceita lixo em volta e entende chave dentro de chave — a primeira tentativa
    com expressao regular quebrava no `args`), e a fala vira o que o dono
    deveria ter ouvido: o texto ANTES do bloco, ou o `fala` de dentro dele.

    Conserto do lado do PC de proposito: a VPS e do Diego, mas quem sofre o
    defeito e o cliente, e este caminho pega qualquer bagunca que chegue.
    """
    if not isinstance(r, dict):
        return {}
    if r.get("ferramenta"):
        return r                      # veio certo: nao mexe
    fala = str(r.get("fala") or "")
    if "{" not in fala:
        return r
    dec = json.JSONDecoder()
    blocos, i = [], fala.find("{")
    while i >= 0:
        try:
            obj, fim = dec.raw_decode(fala, i)
        except ValueError:
            i = fala.find("{", i + 1)
            continue
        if isinstance(obj, dict):
            blocos.append((i, obj))
        i = fala.find("{", max(fim, i + 1))
    if not blocos:
        return r
    novo = dict(r)
    # O texto de verdade e o que veio ANTES do primeiro bloco.
    antes = fala[:blocos[0][0]].strip()
    dentro = ""
    for _pos, obj in blocos:
        if obj.get("ferramenta") and not novo.get("ferramenta"):
            novo["ferramenta"] = obj.get("ferramenta")
            novo["args"] = obj.get("args") if isinstance(obj.get("args"), dict) else {}
        if obj.get("faltam"):
            novo["faltam"] = True
        if not dentro and isinstance(obj.get("fala"), str):
            dentro = obj["fala"].strip()
    novo["fala"] = antes if len(antes) >= 12 else (dentro or antes)
    return novo


_DESCRICAO_PARA_O_CODEX = {
    "screen_process": (
        "O PC tira a foto da tela (ou da webcam) E JA ANALISA a imagem por conta "
        "propria; voce recebe a RESPOSTA EM TEXTO. Chame sempre que o dono "
        "perguntar o que aparece na tela, pedir para olhar a tela ou a camera, ou "
        "quiser que voce leia algo que esta na tela. Use `text` para dizer o que "
        "olhar. Voce NAO precisa enxergar imagem para isso."),
    "marcar_na_tela": (
        "O PC tira o print, CIRCULA em vermelho o que o dono procura e mostra a "
        "imagem para ele. Voce recebe so a confirmacao em texto."),
    "vision_control": (
        "Liga/desliga o controle do mouse por GESTOS da mao pela webcam. NAO serve "
        "para abrir a webcam nem para ver a tela."),
    # 05/09/2026: "cria uma imagem de um robo futurista" gastou 57 segundos e
    # terminou em "a ferramenta nao esta disponivel". Ela estava na lista — com
    # o nome mais opaco de todos (`codex_remote`) e a descricao "Creation tool
    # for customer-facing websites and image files". Ninguem liga uma coisa a
    # outra. Aqui ela diz o que faz, nas palavras que o dono usa.
    "codex_remote": (
        "CRIA IMAGEM OU SITE DO ZERO, pelo servidor. Use kind='image' SEMPRE que "
        "o dono pedir para CRIAR/GERAR/FAZER/DESENHAR uma imagem, arte, desenho, "
        "logo, ilustracao, capa, wallpaper ou foto artificial — passe em "
        "`description` o pedido dele inteiro, com todos os detalhes. Use "
        "kind='site' para criar uma pagina ou site. Demora alguns minutos e o "
        "arquivo pronto chega no computador do dono."),
    "editar_imagem": (
        "EDITA UMA IMAGEM QUE O DONO JA TEM (a que ele arrastou para a janela ou "
        "uma no computador dele): trocar cor, tirar ou por elemento, mudar fundo, "
        "melhorar. Para criar do zero e o `codex_remote` com kind='image'."),
}


def _resumo_das_ferramentas(ferramentas_openai: list) -> list:
    """Uma linha por ferramenta: nome — para que serve — parametros.

    O Codex nao recebe o JSON Schema inteiro (seriam ~6.600 fichas e ele nao usa
    `function calling` nativo aqui): recebe a MESMA informacao em texto, que e o
    que ele precisa para escolher. Medido: com este resumo ele acertou
    `abrir_camera` na primeira, com os argumentos certos.
    """
    linhas = []
    for f in (ferramentas_openai or []):
        fn = (f or {}).get("function") or f or {}
        nome = str(fn.get("name") or "").strip()
        if not nome:
            continue
        # 400 e nao 260: varias ferramentas do JARVIS explicam NO TEXTO quais
        # valores o parametro aceita (o `file_controller` lista as acoes ali),
        # e cortar em 260 comia justamente essa parte. O total ainda fica em
        # ~2.000 fichas, contra 6.635 do esquema JSON inteiro.
        desc = _DESCRICAO_PARA_O_CODEX.get(
            nome, " ".join(str(fn.get("description") or "").split())[:400])
        props = ((fn.get("parameters") or {}).get("properties") or {})
        obrig = set((fn.get("parameters") or {}).get("required") or [])
        partes = []
        # SEIS PARAMETROS, NAO OITO (05/09/2026). A lista inteira ia em TODA
        # volta e somava ~2.800 fichas; com a via rapida da OpenAI isso passou a
        # queimar o teto de fichas por minuto da conta do Diego — e quando o
        # teto estoura, a VOZ (que sai da mesma conta) engasga junto: medi um
        # "primeiro som" de 9,8 s logo depois de um 429. Nenhuma ferramenta do
        # JARVIS precisa de mais de seis parametros para ser escolhida; os que
        # sobram sao detalhe que o executor resolve.
        for p, d in list(props.items())[:6]:
            d = d or {}
            tipo = str(d.get("type") or "").lower()
            marca = "" if p in obrig else "?"
            # OS VALORES ACEITOS VAO JUNTO (04/09/2026). No primeiro teste ele
            # mandou {"action":"criar pasta"} numa ferramenta cujo `action` so
            # aceita palavras fixas — o nome estava certo, o valor nao. Sem a
            # lista, ele advinha em portugues e a ferramenta recusa; com ela,
            # escolhe entre o que existe. Custa poucas fichas e evita a volta.
            opcoes = d.get("enum") or (d.get("items") or {}).get("enum") or []
            # A LISTA QUE MORA NA DESCRICAO (01/10/2026). Muitos parametros do
            # JARVIS nao tem `enum`: os valores vem escritos ("connect | status |
            # send_text | ..."). Cortada em 30 caracteres, a lista do WhatsApp
            # parava em "connect | status | disconnect" — o cerebro nunca via
            # `send_text` e mandava "connect" para enviar mensagem. O mesmo com
            # file_controller (sem delete/move/rename), browser_control e
            # computer_control. Agora a lista vira opcao, inteira.
            _texto_longo = False
            if not opcoes:
                _desc_p = " ".join(str(d.get("description") or "").split())
                _lista = re.search(r"[\w.-]+(?:\s*\|\s*[\w.-]+){2,}", _desc_p)
                if _lista:
                    _antes = _desc_p[:_lista.start()]
                    # So e lista de palavras soltas quando abre a descricao ou
                    # vem depois de "rotulo:". "bloco de notas | word | vscode"
                    # virava ["notas", "word", ...] e o cerebro mandava
                    # janela="notas"; "paint (padrao) | photoshop" perdia o
                    # paint. Nesses casos vai o TEXTO, com folga de 80.
                    if not _antes or (_antes.rstrip().endswith(":") and "|" not in _antes):
                        opcoes = [x.strip() for x in _lista.group(0).split("|") if x.strip()]
                    else:
                        _texto_longo = True
            # E QUANDO NAO HA `enum`, OS VALORES ESTAO NA DESCRICAO DO PARAMETRO.
            #
            # Foi o que derrubou "abre a webcam" no teste de 04/09: o
            # `vision_control` aceita start|stop|status|restart, e isso esta
            # escrito na descricao do proprio `action` — que eu nao mandava. O
            # Codex escolheu a ferramenta CERTA e inventou a acao, e o JARVIS
            # respondeu "nao consegui abrir sua webcam" com o log dizendo
            # "Acao invalida". Uma linha de 70 caracteres resolve.
            # 55 -> 30 caracteres. O que importa aqui e a DICA do valor
            # ("start|stop", "caminho da pasta"), nao a explicacao inteira: o
            # resto sao fichas gastas em toda pergunta do dia. Quando a dica e
            # uma lista de opcoes, ela vem no `enum` acima e nao e cortada.
            d_desc = " ".join(str(d.get("description") or "").split())[:80 if _texto_longo else 30]
            if opcoes:
                partes.append(f"{p}{marca}:{'|'.join(str(o) for o in list(opcoes)[:30])}")
            elif d_desc:
                partes.append(f"{p}{marca}={d_desc}")
            else:
                partes.append(f"{p}{marca}:{tipo or 'texto'}")
        # A DESCRICAO VEM ANTES DOS PARAMETROS (05/09/2026).
        #
        # A linha e cortada no caminho (a ponte e a VPS tem teto por linha), e
        # com os parametros na frente o corte comia justamente a parte que diz
        # PARA QUE A FERRAMENTA SERVE. Dez das trinta e cinco chegavam assim —
        # entre elas o `file_controller`, que e quem cria e move arquivo. Dai o
        # "a ferramenta necessaria nao esta disponivel" que o Diego ouviu: para
        # o modelo, ela realmente nao existia.
        linhas.append(f"{nome} — {desc} | parametros: {', '.join(partes) or '(nenhum)'}")
    return linhas


class CerebroCodex:
    """Pensa pela ponte da VPS. Mesma interface do CerebroGemini."""

    # Teto de idas e voltas por pergunta. Cada volta custa ~5 s.
    #
    # ERA 2 (decidir a ferramenta + contar o resultado) e virou 8 em
    # 05/09/2026, quando o Diego pediu tarefa de VARIOS passos: "abre o bloco
    # de notas, digita um script de calculadora e salva o txt na area de
    # trabalho" sao tres ferramentas num pedido so. Oito voltas cobrem uma
    # tarefa dessas com folga e ainda param antes de virar espera sem fim —
    # e quem diz que acabou e o modelo, pelo campo `faltam`, nao o teto.
    MAX_VOLTAS = 8
    # Tempo maximo de uma tarefa encadeada. Passou disso, ele para e CONTA o
    # que fez ate ali: melhor um relato honesto do que o dono no escuro.
    TEMPO_MAXIMO_S = 120.0

    def __init__(self, ponte=None) -> None:
        self.ponte = ponte
        self.job_id = secrets.token_hex(8)
        self.nome = "JARVIS"
        self.jeito = ""
        # Voz para os passos do meio de uma tarefa encadeada. Quem injeta e o
        # orquestrador; sem ela o dono so ouve o fim (e ate la fica no escuro).
        self.falar = None
        # Uma tarefa de varios passos em andamento (o ultimo passo veio com
        # `faltam`). Enquanto durar, quem decide e o Codex do servidor.
        self._em_cadeia = False

    def _tem_ponte(self) -> bool:
        return bool(self.ponte is not None and hasattr(self.ponte, "pensar_pela_ponte"))

    def disponivel(self) -> bool:
        # A ponte OU a Gemini do proprio cliente (01/10/2026): com a chave dele
        # o JARVIS pensa mesmo com o servidor fora do ar.
        if self._tem_ponte():
            return True
        try:
            from core import cerebro_rapido as _cr  # noqa: PLC0415
            return _cr.disponivel()
        except Exception:
            return False

    def _pensar(self, **kw) -> dict:
        """UMA volta de decisao: Gemini do cliente primeiro, a ponte como rede.

        01/10/2026: a OpenAI do servidor ficou sem credito e o servidor caiu no
        Codex (6 a 20 s por resposta). O Diego: "tem que continuar rapido igual
        estava antes, porem sem depender da OpenAI" — e cada cliente com a
        PROPRIA chave Gemini. O `cerebro_rapido` toma aqui a mesma decisao que o
        servidor tomava com a OpenAI (mesmo prompt, mesmo JSON, mesmas travas)
        em ~0,8 s. Falhou por qualquer motivo (cota, chave, rede), a pergunta
        segue pela ponte exatamente como antes.

        O que NAO vem para ca: a FOTO (`modo="imagem"`, socorro da visao) e os
        passos do MEIO de uma tarefa encadeada — la quem conduz continua sendo o
        Codex, pelo mesmo motivo de 05/09/2026 (o modelo rapido anunciava o passo
        e largava o ultimo, que e o que salva o arquivo do dono).
        """
        modo = str(kw.get("modo") or "")
        usar_rapido = False
        if modo != "imagem" and not (modo == "" and self._em_cadeia):
            try:
                from core import cerebro_rapido as _cr  # noqa: PLC0415
                usar_rapido = _cr.disponivel()
            except Exception:
                usar_rapido = False
        if not usar_rapido:
            r = self._pela_ponte(dict(kw))
            if modo == "":
                self._em_cadeia = bool((r or {}).get("faltam"))
            return r
        r = self._corrida(kw, modo)
        if modo == "":
            self._em_cadeia = bool((r or {}).get("faltam"))
        return r

    # QUANDO A PONTE ENTRA NA CORRIDA (01/10/2026, primeiro teste do Diego).
    #
    # A fila era em SERIE: a Gemini tinha ate 7 s e so depois a ponte comecava.
    # Num pico da Gemini gratuita ele perguntou "boa tarde, tudo bem?" e
    # esperou 15,1 s — 7 s da Gemini calada mais 8 s do Codex. Agora, passados
    # 3 s sem resposta, a ponte comeca JUNTO, e fala quem chegar primeiro. Numa
    # resposta normal (1 a 2 s) a ponte nem e chamada.
    _CORRIDA_DECISAO_S = 3.0
    _CORRIDA_TEXTO_S = 10.0

    # GOOGLE DOENTE, A PONTE SAI JUNTO (08/10/2026). No dia, os dois flash-lite
    # gratis devolviam 503/504 em quase toda pergunta, e cada fala pagava os 3 s
    # de espera da Gemini antes de a ponte comecar. Agora, se a Gemini falhou
    # (ou perdeu para a ponte) nos ultimos `_GOOGLE_DOENTE_S`, a ponte larga JUNTO
    # na decisao seguinte. Uma resposta rapida da Gemini tira o alerta.
    _GOOGLE_DOENTE_S = 180.0
    _google_doente_ate = 0.0

    def _pela_ponte(self, kw: dict) -> dict:
        if not self._tem_ponte():
            raise RuntimeError("a ponte com a VPS ainda nao subiu")
        # A ponte e o servidor cortam a pergunta em 4.000 letras A PARTIR DO
        # COMECO, e a fala do dono e a ultima coisa dela. Encolhe aqui pelo meio
        # (ver `cerebro_rapido.caber`): sai pedaco da nota, nunca a fala.
        try:
            from core.cerebro_rapido import caber as _caber  # noqa: PLC0415
            kw["pergunta"] = _caber(kw.get("pergunta") or "", 3990)
        except Exception:
            pass
        return self.ponte.pensar_pela_ponte(**kw)

    def _corrida(self, kw: dict, modo: str) -> dict:
        """A Gemini sai na frente; atrasou, a ponte corre junto. Vale a primeira."""
        from core import cerebro_rapido as _cr  # noqa: PLC0415
        trava = threading.Lock()
        chegou = threading.Event()
        caixa: dict = {"r": None, "quem": "", "erros": [], "corredores": 1}

        def entregar(quem: str, r, erro: str = "") -> None:
            with trava:
                if r and caixa["r"] is None:
                    caixa["r"], caixa["quem"] = r, quem
                    chegou.set()
                    return
                if not r:
                    caixa["erros"].append(f"{quem}: {erro or 'sem resposta'}")
                    if len(caixa["erros"]) >= caixa["corredores"]:
                        chegou.set()

        def rapido() -> None:
            try:
                if modo == "texto":
                    r = _cr.escrever(kw.get("pergunta") or "", kw.get("contexto") or {})
                else:
                    r = _cr.decidir(kw.get("pergunta") or "", kw.get("historico") or [],
                                    kw.get("ferramentas") or [], kw.get("contexto") or {},
                                    kw.get("resultado") or "")
                entregar("gemini", r)
            except Exception as exc:
                _anotar("cerebro_rapido_falhou", f"{type(exc).__name__}: {str(exc)[:140]}")
                entregar("gemini", None, str(exc)[:80])

        def ponte() -> None:
            try:
                # Outro `job_id`: se a Gemini ganhar, a pergunta da ponte segue
                # ocupando o servidor por alguns segundos, e a PROXIMA pergunta
                # (no job de sempre) bateria em "ja existe uma pergunta em
                # andamento".
                kw2 = dict(kw)
                kw2["job_id"] = str(kw.get("job_id") or self.job_id) + "-corrida"
                entregar("ponte", self._pela_ponte(kw2))
            except Exception as exc:
                entregar("ponte", None, str(exc)[:80])

        import time as _t  # noqa: PLC0415
        t0 = _t.monotonic()
        threading.Thread(target=rapido, daemon=True, name="corrida_gemini").start()
        largada = self._CORRIDA_TEXTO_S if modo == "texto" else self._CORRIDA_DECISAO_S
        if modo != "texto" and _t.monotonic() < CerebroCodex._google_doente_ate:
            largada = 0.0
        if not chegou.wait(largada) and self._tem_ponte():
            with trava:
                caixa["corredores"] = 2
                # A Gemini pode ter falhado nesse meio tempo: com 2 corredores,
                # o evento so dispara de novo quando a ponte tambem terminar.
                if caixa["r"] is None:
                    chegou.clear()
            _anotar("corrida_ponte_largou", f"Gemini sem resposta em {largada:.0f}s")
            threading.Thread(target=ponte, daemon=True, name="corrida_ponte").start()
        elif chegou.is_set() and caixa["r"] is None and self._tem_ponte():
            # A Gemini falhou rapido (cota, chave): a ponte assume sozinha.
            if modo != "texto":
                CerebroCodex._google_doente_ate = _t.monotonic() + self._GOOGLE_DOENTE_S
            return self._pela_ponte(dict(kw))
        chegou.wait(float(kw.get("timeout") or 80.0) + 5.0)
        if modo != "texto":
            if caixa["quem"] == "gemini" and _t.monotonic() - t0 < 2.5:
                CerebroCodex._google_doente_ate = 0.0
            elif caixa["quem"] != "gemini":
                CerebroCodex._google_doente_ate = _t.monotonic() + self._GOOGLE_DOENTE_S
        if caixa["r"] is None:
            raise RuntimeError("; ".join(caixa["erros"]) or "nenhum cerebro respondeu")
        if caixa["quem"] == "ponte":
            _anotar("corrida_ganha_pela_ponte", f"{_t.monotonic() - t0:.1f}s")
        return caixa["r"]

    # ── por dentro ──────────────────────────────────────────────────────
    @staticmethod
    def _ultima_pergunta(mensagens: list) -> str:
        for m in reversed(mensagens or []):
            if (m or {}).get("role") == "user":
                texto = str(m.get("content") or "").strip()
                # As linhas de servico que o orquestrador cola na pergunta (o
                # relogio, o lembrete do jeito) nao sao a fala do dono.
                if texto and not texto.startswith(("AGORA SAO", "LEMBRETE", "PARE.")):
                    return texto
        return ""

    @staticmethod
    def _historico(mensagens: list) -> list:
        saida = []
        for m in (mensagens or [])[1:]:      # [0] e o prompt de sistema
            papel = (m or {}).get("role")
            texto = str((m or {}).get("content") or "").strip()
            if not texto or papel not in ("user", "assistant"):
                continue
            if texto.startswith(("AGORA SAO", "LEMBRETE")):
                continue
            # A NOTA INTERNA NAO E FALA (01/10/2026). A mensagem do dono chega
            # com "[NOTA INTERNA ...]" na frente; cortada em 300 letras, o
            # historico guardava so o comeco da nota e nunca o que ele disse.
            if papel == "user" and texto.startswith("[") and "]\n\n" in texto:
                texto = texto.rsplit("]\n\n", 1)[-1].strip()
                if not texto:
                    continue
            saida.append(("dono: " if papel == "user" else "voce: ") + texto[:300])
        return saida[-8:]

    def _contexto(self) -> dict:
        from datetime import datetime  # noqa: PLC0415
        semana = ("segunda", "terca", "quarta", "quinta", "sexta", "sabado", "domingo")
        agora = datetime.now()
        ctx = {
            "jeito": self.jeito or "",
            "nome": self.nome or "JARVIS",
            "agora": f"{semana[agora.weekday()]}, {agora.strftime('%d/%m/%Y %H:%M')}",
        }
        # CLIENTE EM INGLES (26/09/2026): o cerebro da VPS troca a regra de
        # lingua do prompt dele. A LANGUAGE RULE que ia so dentro da pergunta
        # nao bastava — o prompt da VPS manda portugues e trata o texto do dono
        # como pedido, nunca como regra.
        try:
            from core.idioma import em_ingles  # noqa: PLC0415
            if em_ingles():
                ctx["idioma"] = "en"
        except Exception:
            pass
        return ctx

    # ── a porta ─────────────────────────────────────────────────────────
    def responder(self, mensagens: list, ferramentas_openai: list, executor,
                  forcar_ferramenta: bool = False) -> dict:
        """Devolve {"texto": ..., "chamou_ferramenta": bool}.

        Levanta excecao quando a ponte nao responde — quem chama decide o que
        fazer (hoje: avisar o dono, sem inventar resposta).
        """
        if not self.disponivel():
            raise RuntimeError("a ponte com a VPS ainda nao subiu")
        pergunta = self._ultima_pergunta(mensagens)
        if not pergunta:
            return {"texto": "", "chamou_ferramenta": False}
        pergunta = _com_a_memoria(pergunta)
        pergunta_original = pergunta
        # Fala nova do dono: tarefa encadeada que tenha ficado pela metade nao
        # prende esta pergunta no caminho lento.
        self._em_cadeia = False

        ferramentas = _resumo_das_ferramentas(ferramentas_openai)
        historico = self._historico(mensagens)
        contexto = self._contexto()
        if forcar_ferramenta and ferramentas:
            # A trava anti-mentira do orquestrador cobrando de novo: aqui ela
            # vira instrucao no texto, porque o Codex nao tem o modo "ANY" da
            # Gemini. Sem isso ele pede desculpa e continua sem fazer nada.
            pergunta = (f"{pergunta}\n\n(ATENCAO: este pedido EXIGE chamar uma "
                        f"ferramenta da lista. Nao responda so com fala.)")

        import time as _t  # noqa: PLC0415
        chamou = False
        texto = ""
        resultado = ""
        feitos: list[str] = []
        # O que ja foi executado NESTE turno, com o resultado. Ver o comentario
        # de `ferramenta_repetida` mais abaixo: e o que impede a frase de ser
        # digitada duas vezes no bloco de notas.
        ja_chamadas: dict = {}
        t0 = _t.monotonic()
        for _volta in range(self.MAX_VOLTAS):
            if _t.monotonic() - t0 > self.TEMPO_MAXIMO_S:
                # Tarefa longa demais: conta o que deu para fazer e para.
                try:
                    from core.idioma import em_ingles  # noqa: PLC0415
                    if em_ingles():
                        return {"texto": ("I ran out of time in the middle of the task, "
                                          "so I stopped." if not feitos else
                                          "I ran out of time in the middle of the task "
                                          "and stopped there."),
                                "chamou_ferramenta": chamou}
                except Exception:
                    pass
                return {"texto": _sem_marcacao(
                    ("Passei do tempo no meio da tarefa. Ate aqui eu " + ", ".join(feitos[-3:]) + ".")
                    if feitos else "Passei do tempo e parei no meio da tarefa."),
                    "chamou_ferramenta": chamou}
            r = self._pensar(
                job_id=self.job_id, pergunta=pergunta, historico=historico,
                ferramentas=ferramentas, contexto=contexto, resultado=resultado)
            # A CHAMADA DA FERRAMENTA AS VEZES VEM ESCRITA DENTRO DA FALA.
            # Sem este resgate ela nao e executada e ainda vai parar no chat.
            r = _resgatar_json(r)
            texto = str((r or {}).get("fala") or "").strip()
            nome = str((r or {}).get("ferramenta") or "").strip()
            # `faltam`: o MODELO dizendo que o plano dele nao acabou. E ele quem
            # sabe — eu so vejo um pedido em portugues. Sem este campo, ou eu
            # perguntaria sempre (e o pedido simples pagava 5 s a mais) ou nunca
            # (e "abre, digita e salva" pararia no "abre").
            faltam = bool((r or {}).get("faltam"))
            if not nome:
                break
            args = (r or {}).get("args")
            if not isinstance(args, dict):
                args = {}
            if executor is None:
                # QUEM NAO TEM EXECUTOR NAO CHAMA FERRAMENTA (05/09/2026).
                #
                # Este caminho e usado tambem para PEDIR TEXTO ("escreva um
                # script de calculadora"), e ali nao ha ferramenta nenhuma para
                # executar. Antes o codigo respondia "Ferramenta indisponivel."
                # e esse texto virava o CONTEUDO — foi o que o JARVIS digitou
                # dentro do bloco de notas do dono. Agora o pedido de ferramenta
                # e ignorado e vale o que ele falou.
                break
            chamou = True
            # O AVISO SAI ENQUANTO A FERRAMENTA TRABALHA (05/09/2026).
            #
            # "Pesquisa as ultimas noticias" levava 14,4 s: 4,5 s o Codex
            # decidindo, 2,3 s pesquisando, 7 s ele resumindo — e o dono ficava
            # os 14 s inteiros no escuro, sem saber se tinha sido ouvido. O
            # tempo total nao muda (a volta do cerebro e 4 s medidos, e nao ha
            # o que cortar ali), mas a ESPERA CALADA muda: ele ouve na hora que
            # o JARVIS entendeu e ja esta trabalhando.
            #
            # Numa linha separada, senao a fala (1,5 s) seria somada ao total.
            # E so para ferramenta LENTA: em "abre o bloco de notas" o resultado
            # ja vem em menos de um segundo, e dizer "abrindo" antes de "aberto"
            # seria falar duas vezes para nao adiantar nada.
            avisou_antes = False
            if (nome in _FERRAMENTAS_DEMORADAS and texto
                    and callable(getattr(self, "falar", None))):
                threading.Thread(target=self._avisar_que_comecou,
                                 args=(texto,), daemon=True).start()
                avisou_antes = True
            # A MESMA CHAMADA NAO ACONTECE DUAS VEZES (05/09/2026).
            #
            # O Diego pediu uma frase de bom dia no bloco de notas e a frase
            # apareceu DUAS VEZES na tela — o registro mostra
            # "[digitar] 85 caracteres em 1s" repetido. O modelo, na volta
            # seguinte, pediu a MESMA ferramenta com os MESMOS argumentos, e o
            # executor obedeceu de novo.
            #
            # Aqui a chamada repetida devolve o resultado guardado, sem
            # executar: o efeito nao acontece duas vezes, o modelo recebe a
            # resposta que ele ja tinha e segue para o fecho. De quebra economiza
            # a volta inteira ("pensar 29,9s" naquele turno).
            #
            # Vale para TUDO de proposito. Repetir "que horas sao" so gasta
            # tempo; repetir "digita", "salva" ou "manda mensagem" estraga o
            # trabalho do dono — e nao da para saber de fora qual e qual.
            _assinatura = nome + "|" + json.dumps(args, sort_keys=True,
                                                  ensure_ascii=False)[:400]
            if _assinatura in ja_chamadas:
                saida = ja_chamadas[_assinatura]
                try:
                    from main import _voice_diag  # noqa: PLC0415
                    _voice_diag("ferramenta_repetida",
                                f"{nome}: ja tinha sido feita neste turno — nao repeti")
                except Exception:
                    pass
            else:
                try:
                    saida = executor(nome, args)
                except Exception as exc:
                    saida = f"A ferramenta falhou: {type(exc).__name__}: {str(exc)[:200]}"
                ja_chamadas[_assinatura] = saida
            feitos.append(f"{nome}")
            # PEDIDO DE DUAS ACOES COM UMA FEITA SO (01/10/2026). Na varredura,
            # 1 vez em 9 o modelo rapido abria o Bloco de Notas SEM marcar
            # `faltam` e o "escreve uma lista" sumia. Quando a nota do
            # orquestrador avisou que o pedido tem mais de uma acao e so uma foi
            # feita, ele volta UMA vez com o resultado na mao — se ja estiver
            # tudo feito, o fecho diz isso; se nao, sai o passo que faltava.
            if (not faltam and len(feitos) == 1 and "MAIS DE UMA acao" in str(pergunta_original)
                    and nome not in _FERRAMENTAS_QUE_JA_RESPONDEM):
                faltam = True
                _anotar("cadeia_conferida", f"{nome}: pedido de varias acoes, volto para o resto")
            # O PASSO INTERMEDIARIO E FALADO NA HORA, se quem chamou deu voz.
            # Numa tarefa de tres passos o dono ouve "abrindo o bloco de notas",
            # "escrevendo o programa", "salvando" — em vez de dois minutos de
            # silencio e uma frase no fim. Mesma regra do computer use.
            if faltam and texto and not avisou_antes and callable(getattr(self, "falar", None)):
                try:
                    self.falar(texto)
                except Exception:
                    pass
            # A RESPOSTA JA ESTA PRONTA? ENTAO NAO PERGUNTE DE NOVO.
            #
            # (04/09/2026, queixa do Diego: "ele abre o bloco de notas na hora,
            # mas demora dez segundos para DIZER que abriu".) A segunda volta
            # ao Codex existia so para transformar o resultado da ferramenta
            # numa frase — e custa mais 4 a 9 s. So que a maioria das
            # ferramentas do JARVIS ja devolve a frase pronta, em portugues:
            # "Bloco de Notas aberto.", "Nao consegui abrir a webcam". Quando e
            # esse o caso, a fala sai NA HORA e a volta e economizada.
            #
            # A segunda volta continua existindo para o resultado que nao serve
            # de fala: uma lista de arquivos, um JSON, um erro tecnico.
            pronto = _resposta_pronta(saida)
            if pronto and not faltam:
                # Um passo so, e o resultado ja e frase: fala na hora.
                texto = pronto
                break
            # FERRAMENTA QUE JA E A RESPOSTA (05/09/2026).
            #
            # "Analisa minha tela" levou 22,9 s: 5,9 s o Codex decidindo olhar,
            # 9,4 s olhando — e mais 7 s so para ele REESCREVER, em outras
            # palavras, a frase que ele mesmo tinha acabado de dizer sobre a
            # foto. O dono esperou 7 segundos por uma parafrase que ate perdeu
            # detalhe ("parte inferior esquerda" virou "canto inferior
            # esquerdo").
            #
            # Para estas ferramentas o resultado NAO e material para pensar: e
            # a resposta da pergunta. Entao vale mesmo com `faltam`, e cabe
            # frase mais longa que o limite normal — descricao de tela e assim.
            if nome in _FERRAMENTAS_QUE_JA_RESPONDEM:
                ja = _sem_marcacao(str(saida or "").strip())
                if 8 < len(ja) <= 600 and not _TECNICO.search(ja) and not _precisa_traduzir(ja):
                    _anotar("cerebro_atalho", f"{nome}: falei o resultado sem segunda volta")
                    texto = ja
                    break
            resultado = f"{nome}({json.dumps(args, ensure_ascii=False)[:300]}) -> {str(saida)[:1500]}"
            # A PROXIMA VOLTA: com `faltam`, e o proximo PASSO da tarefa; sem
            # ele, e so o fecho contando o que aconteceu. Nos dois casos o
            # pedido original fica no `resultado`, e o que ele ja falou entra no
            # historico — senao ele repete a mesma frase no passo seguinte.
            pergunta = ""
            if texto:
                historico = historico + [f"voce: {texto[:200]}"]
        texto = _sem_marcacao(texto)
        if texto and _precisa_traduzir(texto):
            texto = self._traduzir_para_ingles(texto) or texto
        return {"texto": texto, "chamou_ferramenta": chamou}

    def _traduzir_para_ingles(self, texto: str) -> str:
        """REDE DE SEGURANCA do cliente em ingles (26/09/2026).

        No log do cliente do Canada: ele falava "Can you hear me?" e ouvia
        "Sim, estou ouvindo voce!". Se, mesmo com a regra, a resposta vier em
        portugues, ela e traduzida aqui antes de ir para a voz. Custa uma volta
        a mais (~1 s), so quando precisa.
        """
        try:
            saida = self.escrever(
                "Translate the text below into natural, fluent spoken English. "
                "Reply with ONLY the English translation: no quotes, no notes.",
                texto, cru=True)
            saida = _sem_marcacao(str(saida or "").strip())
            if saida and not _precisa_traduzir(saida):
                _anotar("traduzi_para_ingles", f"{texto[:50]!r} -> {saida[:50]!r}")
                return saida
            _anotar("traducao_falhou", f"voltou {saida[:50]!r}")
        except Exception as exc:
            _anotar("traducao_falhou", str(exc)[:90])
        return ""

    def _avisar_que_comecou(self, texto: str) -> None:
        """Fala o aviso do meio da tarefa sem segurar a tarefa."""
        try:
            self.falar(texto)
        except Exception:
            pass

    def escrever(self, instrucao: str, pedido: str, cru: bool = False) -> str:
        """Pede CONTEUDO (nao acao): texto puro de volta, sem ferramenta.

        Caminho separado de proposito — ver o `modo texto` no jarvis-cerebro.js.
        Misturar os dois foi o que fez o JARVIS digitar "nao ha ferramenta
        disponivel" dentro do bloco de notas do dono.
        """
        if not self.disponivel():
            raise RuntimeError("a ponte com a VPS ainda nao subiu")
        ctx = self._contexto()
        ctx["instrucao"] = str(instrucao or "")[:800]
        # `cru` = quem vai LER a resposta e um programa, nao o dono. Sem isso,
        # a ferramenta que espera `{"action":...}` recebe uma frase educada e
        # quebra no json.loads — erro tecnico na cara do cliente.
        if cru:
            ctx["formato"] = "cru"
        r = self._pensar(
            job_id=self.job_id, pergunta=str(pedido or ""), historico=[],
            ferramentas=[], contexto=ctx, modo="texto", timeout=120.0)
        return str((r or {}).get("fala") or "").strip()

    def ver(self, imagem: bytes, pergunta: str, cru: bool = False) -> str:
        """Manda uma FOTO e devolve o que o Codex enxergou nela.

        O socorro da visao (05/09/2026). Ate aqui, "olha minha tela" dependia
        so da chave gratuita da Gemini do cliente: acabou a cota do dia, a
        funcao sumia ate o dia seguinte. Agora ha um piso sem cota — o mesmo
        Codex que ja analisa a foto da tela no computer use.
        """
        if not self.disponivel():
            raise RuntimeError("a ponte com a VPS ainda nao subiu")
        if not imagem:
            return ""
        ctx = self._contexto()
        ctx["instrucao"] = str(pergunta or "")[:800]
        if cru:                                   # ver o `cru` do `escrever`
            ctx["formato"] = "cru"
        r = self._pensar(
            job_id=self.job_id + "-olho", pergunta=str(pergunta or ""), historico=[],
            ferramentas=[], contexto=ctx, modo="imagem", imagem=imagem, timeout=120.0)
        # SERVIDOR VELHO: um cerebro anterior a 05/09/2026 nao conhece o modo
        # imagem — ele IGNORA a foto e responde como se fosse uma decisao de
        # ferramenta. Deixar isso passar seria pior que falhar: o JARVIS falaria
        # sobre uma tela que ninguem olhou.
        if (r or {}).get("ferramenta"):
            raise RuntimeError("a VPS ainda esta com o cerebro antigo (sem modo imagem)")
        return str((r or {}).get("fala") or "").strip()

    def encerrar(self) -> None:
        try:
            if self._tem_ponte():
                self.ponte.encerrar_conversa_na_ponte(self.job_id)
        except Exception:
            pass


# O que a ferramenta devolveu ja serve como fala?
#
# Serve quando e uma frase curta de gente ("Bloco de Notas aberto.", "Nao
# consegui abrir a webcam"). NAO serve quando e dado para o modelo ler: lista de
# arquivos, JSON, log, marca tecnica (SAVE_AS_OK), texto longo. Nesses casos a
# segunda volta ao Codex vale os segundos que custa, porque so ele sabe resumir.
_TECNICO = re.compile(r"[{}\[\]<>|]|\b(SAVE_AS_OK|SAVE_AS_FAILED|None|null|true|false)\b|"
                      r"^\s*(\w+_){2,}", re.I)


# Ferramentas que levam mais de uns 2 s — nelas vale avisar antes de comecar,
# senao o dono fica no escuro achando que nao foi ouvido. Medido no PC dele:
# web_search 2,3 s, screen_process 7 a 9 s, imagem e site em minutos.
_FERRAMENTAS_DEMORADAS = {
    "web_search", "search_news", "buscar_noticias", "screen_process",
    "dev_agent", "code_helper", "file_processor", "criar_imagem",
    "editar_imagem", "criar_site", "buscar_leads", "flight_finder",
    "youtube_video",
    # `usar_computador` e `executar_tarefa` NAO entram: elas ja narram sozinhas
    # (a faixa na tela e o aviso de 2 em 2 minutos). Avisar por fora seria duas
    # vozes tentando falar em cima da outra.
}

# Ferramentas cujo resultado E a resposta do dono, nao material para pensar.
# Olhar a tela ja e uma pergunta respondida; mandar isso de volta ao Codex so
# gasta uma volta inteira para trocar as palavras de lugar.
_FERRAMENTAS_QUE_JA_RESPONDEM = {"screen_process"}


def _anotar(evento: str, detalhe: str = "") -> None:
    """Uma linha no diario do JARVIS (`logs/voice_session.log`).

    O `print` daqui nao aparece: o JARVIS roda no `pythonw`. E entender por que o
    cerebro demorou foi, tres vezes esta semana, o trabalho mais caro do dia.
    """
    try:
        from datetime import datetime as _dt  # noqa: PLC0415
        from pathlib import Path as _P        # noqa: PLC0415
        caminho = _P(__file__).resolve().parents[1] / "logs" / "voice_session.log"
        caminho.parent.mkdir(parents=True, exist_ok=True)
        with open(caminho, "a", encoding="utf-8") as f:
            f.write(f"{_dt.now().isoformat(timespec='milliseconds')}\t{evento}\t{detalhe}\n")
    except Exception:
        pass


def _resposta_pronta(saida) -> str:
    """A frase que a ferramenta ja devolveu pronta para falar, ou ""."""
    texto = str(saida or "").strip()
    if _precisa_traduzir(texto):
        return ""   # cliente em ingles: a segunda volta traduz antes de falar
    if not texto or len(texto) > 220 or "\n" in texto:
        return ""
    if _TECNICO.search(texto):
        return ""
    # Precisa parecer frase: pelo menos duas palavras e uma letra de verdade.
    if len(texto.split()) < 2 or not re.search(r"[A-Za-zÀ-ÿ]{3}", texto):
        return ""
    return _sem_marcacao(texto)


# RECUSA NAO E CONTEUDO (05/09/2026).
#
# Quando o cerebro e chamado para ESCREVER algo ("um script de calculadora"), a
# resposta dele e o texto que sera digitado no computador do dono. Se ele
# responde "nao consigo, nenhuma ferramenta esta disponivel", isso NAO e o
# conteudo — mas era tratado como se fosse, e o JARVIS digitou essa frase dentro do
# bloco de notas do Diego. Duas vezes, com o dono olhando.
_RECUSA = re.compile(
    r"\b(nao consigo|nao posso|nao tenho|nao foi possivel|nao ha ferramenta|"
    r"nao existe ferramenta|indisponivel|nenhuma ferramenta|sem ferramenta|"
    r"desculpe|infelizmente|nao sera possivel|nao da para)\b", re.I)


def parece_recusa(texto: str) -> bool:
    """O que voltou e uma desculpa, e nao o conteudo pedido?"""
    t = str(texto or "").strip()
    if not t or len(t) > 400:
        return False          # texto longo e conteudo de verdade
    import unicodedata as _ud  # noqa: PLC0415
    sem = "".join(c for c in _ud.normalize("NFD", t.lower())
                  if _ud.category(c) != "Mn")
    return bool(_RECUSA.search(sem))


_MARCACAO = re.compile(r"[*_`#]+")


def _sem_marcacao(texto: str) -> str:
    """Tira asterisco e afins: isto vai ser FALADO, nao lido na tela."""
    return _MARCACAO.sub("", str(texto or "")).strip()
