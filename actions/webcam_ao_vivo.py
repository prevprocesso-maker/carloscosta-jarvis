# -*- coding: utf-8 -*-
"""A webcam vista em MOVIMENTO, e nao numa foto so — pedido do Diego (05/09/2026).

"Ela consegue ver em tempo real minha webcam? Manda vários frames pro Codex CLI."

O que existia: `screen_process(angle="camera")` tira UMA foto e pergunta o que
aparece nela. Serve para "o que voce ve", nao para "o que esta acontecendo": uma
foto nao mostra aceno, nem objeto entrando na frente, nem se a pessoa saiu.

O que este arquivo faz: pega VARIOS quadros espacados no tempo, monta os quatro
numa imagem so (grade 2x2, numerados) e manda essa imagem unica para o Codex,
dizendo que sao momentos consecutivos.

POR QUE UMA IMAGEM SO, e nao quatro pedidos: cada ida a VPS custa segundos. Com
quatro pedidos o dono esperaria quatro vezes e o modelo veria cada quadro sem
saber dos outros — sem os outros nao existe movimento, que e justamente o que
ele quer. Numa grade, a comparacao esta dentro da propria imagem.

LIMITE HONESTO, para nao prometer o que nao e: isto NAO e video ao vivo continuo.
Sao ~2 segundos de janela amostrados em quatro instantes; o dono pergunta e ele
responde sobre o que aconteceu naquela janela.
"""
from __future__ import annotations

import io
import time

# Quantos quadros e por quanto tempo. Quatro em 2 s foi o que coube: menos que
# isso nao mostra movimento, mais que isso comeca a fazer o dono esperar antes
# mesmo de a pergunta sair da maquina.
QUADROS = 8
JANELA_S = 3.0
# Grade 4 colunas x 2 linhas. Com 8 quadros a 2x2 nao serve mais.
COLUNAS = 4


def _capturar_quadros(player=None) -> list[bytes]:
    """Os quadros da webcam, espacados no tempo. Lista vazia se nao der.

    DUAS CAPTURAS NA MESMA WEBCAM NAO CONVIVEM (05/09/2026). O Diego viu a
    camera abrir e ficar CONGELADA enquanto o JARVIS "olhava": a tela estava com
    o aparelho aberto e o `_capture_camera` tentava abrir de novo, roubando o
    dispositivo — a imagem parava, e o que ele analisava eram quadros velhos.
    (O `_capture_camera` so reaproveita o quadro da tela quando ele passa de
    18 KB; abaixo disso ele abre o aparelho, e foi por ali que escapou.)

    Com a camera da tela ligada, os quadros saem DELA — que e quem esta com o
    aparelho e continua rodando ao vivo, a 65 de qualidade JPEG, tamanho cheio.
    So quando nao ha camera na tela e que o caminho antigo entra.
    """
    intervalo = JANELA_S / max(1, QUADROS - 1)
    quadros: list[bytes] = []
    pegar_da_tela = getattr(player, "get_latest_camera_frame_bytes", None)
    vistos = set()

    for i in range(QUADROS):
        dados = None
        if callable(pegar_da_tela):
            try:
                # `max_age` curto: quadro velho nao serve, e a ausencia dele e
                # o sinal de que a camera da tela nao esta mesmo rodando.
                dados = pegar_da_tela(max_age=1.5)
            except Exception:
                dados = None
        if not dados:
            try:
                from actions.screen_processor import _capture_camera  # noqa: PLC0415
                dados, _mime = _capture_camera(player=player)
            except Exception:
                dados = None
        if dados:
            # Quadro repetido nao mostra movimento nenhum: se a camera ainda
            # nao entregou um novo, espera o proximo giro em vez de duplicar.
            marca = (len(dados), dados[-64:])
            if marca not in vistos:
                vistos.add(marca)
                quadros.append(dados)
        if i < QUADROS - 1:
            time.sleep(intervalo)
    return quadros


def _montar_grade(quadros: list[bytes]) -> bytes:
    """Junta os quadros numa imagem 2x2, numerados na ordem em que sairam."""
    from PIL import Image, ImageDraw  # noqa: PLC0415

    imagens = []
    for d in quadros:
        try:
            imagens.append(Image.open(io.BytesIO(d)).convert("RGB"))
        except Exception:
            pass
    if not imagens:
        return b""
    # Todos do tamanho do primeiro: webcam pode entregar quadro torto no comeco.
    largura, altura = imagens[0].size
    largura = max(160, min(largura, 640))
    altura = max(120, min(altura, 480))
    imagens = [im.resize((largura, altura)) for im in imagens]
    while len(imagens) < QUADROS:
        imagens.append(imagens[-1])
    imagens = imagens[:QUADROS]

    linhas = (len(imagens) + COLUNAS - 1) // COLUNAS
    grade = Image.new("RGB", (largura * COLUNAS, altura * linhas), (0, 0, 0))
    for i, im in enumerate(imagens):
        grade.paste(im, ((i % COLUNAS) * largura, (i // COLUNAS) * altura))
    # O NUMERO IMPORTA: sem ele o modelo nao sabe a ORDEM, e sem ordem nao da
    # para dizer o que veio antes e o que veio depois.
    desenho = ImageDraw.Draw(grade)
    for i in range(len(imagens)):
        x = (i % COLUNAS) * largura + 8
        y = (i // COLUNAS) * altura + 6
        desenho.rectangle([x - 4, y - 2, x + 26, y + 22], fill=(0, 0, 0))
        desenho.text((x, y), str(i + 1), fill=(255, 255, 255))

    saida = io.BytesIO()
    grade.save(saida, format="JPEG", quality=72)
    return saida.getvalue()


def ver_webcam_ao_vivo(pergunta: str = "", player=None) -> str:
    """Olha a webcam em movimento e devolve, em texto, o que aconteceu.

    A CAMERA ABRE NA TELA ENQUANTO ELE OLHA (05/09/2026, pedido do Diego:
    *"quando eu peço pra ele analisar minha webcam ele analisa porém ele não
    abre minha webcam, ele tem que abrir e deixar rodando até pegar todos os
    frames"*).

    Nao ha briga pelo aparelho: com a camera da tela aberta, o
    `_capture_camera` reaproveita o quadro que ela ja esta mostrando (ver
    `_latest_frame_from_player`) em vez de abrir o dispositivo de novo — que no
    Windows falharia, porque duas capturas na mesma webcam nao convivem.

    Se a camera JA estava aberta, fica aberta no fim: quem abriu foi o dono.
    """
    eu_abri = False
    try:
        if player is not None and hasattr(player, "start_camera_stream"):
            # SE HA QUADRO FRESCO, A CAMERA ESTA RODANDO. Perguntar por uma
            # bandeira (`camera_ligada`) nao servia: a UI nao expoe nenhuma com
            # esse nome, entao a conta dava "fechada" mesmo aberta e o JARVIS
            # fechava no fim a camera que o DONO tinha aberto.
            ja_aberta = False
            try:
                _teste = player.get_latest_camera_frame_bytes(max_age=1.5)
                ja_aberta = bool(_teste)
            except Exception:
                ja_aberta = False
            if not ja_aberta:
                player.start_camera_stream()
                eu_abri = True
                # A CAMERA PRECISA ACORDAR. Um segundo nao bastava: os
                # primeiros quadros saiam escuros e AZULADOS (o sensor ainda
                # ajustando exposicao e balanco de branco), e o modelo,
                # obediente, relatava "a iluminacao fica azulada" em vez de
                # dizer o que o dono estava segurando. Dois segundos, e o
                # primeiro quadro ainda assim vai fora, logo abaixo.
                time.sleep(2.0)
                try:
                    player.get_latest_camera_frame_bytes(max_age=3.0)
                except Exception:
                    pass
    except Exception:
        eu_abri = False

    try:
        quadros = _capturar_quadros(player=player)
    finally:
        if eu_abri:
            try:
                player.stop_camera_stream()
            except Exception:
                pass
    if not quadros:
        return "Nao consegui abrir a webcam agora, chefe."

    grade = _montar_grade(quadros)
    if not grade:
        return "Peguei a webcam, mas nao consegui montar as imagens."

    # O PEDIDO SEGUE A PERGUNTA DELE (05/09/2026). Este texto era todo virado
    # para MOVIMENTO ("o que mudou entre as fotos"), porque nasceu do pedido
    # "ver a webcam em tempo real". So que na maioria das vezes o Diego pergunta
    # O QUE E uma coisa que ele esta segurando — e recebia um relatorio de
    # movimento e de iluminacao em vez do nome do objeto. Ele chamou isso de
    # "bugado esse frame a frame", com razao.
    #
    # Agora: quando ele PERGUNTA algo, a pergunta manda, e as varias fotos
    # servem para escolher a mais nitida. Movimento so quando ele quer movimento.
    quer = str(pergunta or "").strip()
    pedido = (
        f"Estas sao {len(quadros)} fotos da webcam do dono, tiradas em sequencia "
        "ao longo de cerca de tres segundos, numeradas no canto. Elas sao o MESMO "
        "momento visto varias vezes: use a mais NITIDA para enxergar detalhe, e "
        "as outras so para confirmar. Responda em UMA ou DUAS frases, em "
        "portugues do Brasil, falando com ele. Nao descreva foto por foto, nao "
        "fale de iluminacao nem de qualidade da imagem, e nao use lista: isto "
        "vai ser dito em voz alta."
    )
    try:  # cliente em ingles (24/09/2026): ver core/idioma.py
        from core.idioma import no_idioma  # noqa: PLC0415
        pedido = no_idioma(pedido)
    except Exception:
        pass
    if quer:
        pedido += (
            "\n\nO QUE ELE PERGUNTOU, e e isto que voce tem de responder: "
            + quer +
            "\nSe ele perguntou O QUE e alguma coisa (um objeto na mao, algo na "
            "mesa), NOMEIE a coisa com o maximo de certeza que as fotos "
            "permitirem, e nao fale de movimento. Se ele perguntou o que esta "
            "ACONTECENDO ou se voce ve algum gesto, ai sim compare as fotos e "
            "conte o que mudou."
        )
    else:
        pedido += (" Conte o que aparece e, se algo mudou entre as fotos "
                   "(alguem se moveu, acenou, entrou ou saiu), diga o que foi.")

    # O MESMO caminho que o computer use ja usa para a foto da tela.
    from actions.screen_processor import _PONTE_DA_VISAO  # noqa: PLC0415
    if _PONTE_DA_VISAO is None:
        return "Nao consigo olhar a webcam agora: perdi a ligacao com o servidor."
    try:
        from core.cerebro_codex import CerebroCodex  # noqa: PLC0415
        olho = CerebroCodex(ponte=_PONTE_DA_VISAO)
        if not olho.disponivel():
            return "Nao consigo olhar a webcam agora: a ligacao com o servidor caiu."
        texto = olho.ver(grade, pedido)
    except Exception as exc:
        return f"Nao consegui analisar a webcam: {str(exc)[:90]}"
    return str(texto or "").strip() or "Olhei a webcam, mas nao consegui descrever."
