# -*- coding: utf-8 -*-
"""Marcar na tela o que o cliente nao esta achando.

Ideia do Diego em 01/09/2026, e e das melhores que ele teve:

    Cliente: "JARVIS, olha minha tela — voce ve o botao Configuracao?"
    JARVIS:  "Vejo sim, ele fica no canto de baixo a esquerda. Se voce nao
              achar, eu marco na imagem e te mando."
    Cliente: "marca pra mim"
    JARVIS:  tira o print, manda para o Codex na VPS com a instrucao de
             CIRCULAR o item, recebe a imagem marcada, abre na tela e diz
             "pronto, esta marcado".

Por que o Codex e nao a Gemini: o `image_edit` do Codex ja existe, ja recebe a
foto pela ponte e ja devolve a imagem editada — e o mesmo caminho que edita a
foto que o cliente arrasta para a janela. Reaproveitar isso custa nada.

A captura e do MESMO helper do `screen_process`, para o JARVIS marcar exatamente
a tela que ele acabou de olhar.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import time
from pathlib import Path


def marcar_na_tela(parameters: dict, player=None, speak=None) -> str:
    """Tira o print, manda circular o alvo e abre a imagem marcada."""
    params = parameters or {}
    alvo = str(params.get("alvo") or params.get("target") or "").strip()
    if not alvo:
        return ("Me diz o que voce quer que eu marque na tela — o nome do botao, "
                "do menu, do campo.")

    # 1) O print. Mesmo helper do screen_process, para ser a mesma tela.
    try:
        from actions.screen_processor import _capture_screen  # noqa: PLC0415
        imagem, _mime = _capture_screen()
    except Exception as exc:
        return f"Nao consegui capturar a tela: {str(exc)[:120]}"

    destino = Path(tempfile.gettempdir()) / f"jarvis-tela-{int(time.time())}.png"
    try:
        destino.write_bytes(imagem)
    except Exception as exc:
        return f"Nao consegui guardar a captura: {str(exc)[:120]}"

    # 2) O Codex circula. A instrucao e dura de proposito: sem isto ele
    #    "melhora" a imagem inteira, e o cliente perde a referencia da propria
    #    tela — o que ele precisa e reconhecer onde clicar.
    pedido = (
        f"Esta imagem e a captura de tela do computador do usuario. "
        f"Encontre nela: {alvo}. "
        f"Desenhe um CIRCULO VERMELHO GROSSO em volta desse item, e uma seta "
        f"vermelha apontando para ele. "
        f"NAO altere mais NADA da imagem: nao mude cores, nao recorte, nao "
        f"redesenhe a tela, nao acrescente texto explicativo. A imagem tem de "
        f"continuar sendo a captura original, so que com a marcacao por cima. "
        f"Se voce nao encontrar o item, devolva a imagem sem marcacao nenhuma."
    )
    try:
        from actions.codex_remote import remote_codex_job  # noqa: PLC0415
        resultado = remote_codex_job(
            {"kind": "image_edit", "description": pedido,
             "imagem": str(destino), "project_name": "JarvisMarcacao"},
            player=player, speak=speak)
    except Exception as exc:
        return f"O servidor nao conseguiu marcar a imagem: {str(exc)[:140]}"
    finally:
        try:
            destino.unlink()
        except Exception:
            pass

    # 3) NAO abrir aqui.
    #
    # A ponte (`codex_remote`) ja abre a imagem editada sozinha assim que ela
    # chega. Eu abria de novo e o Diego via DUAS janelas da mesma imagem
    # (01/09/2026). Uma abertura basta.
    texto = str(resultado or "")
    if "imagem editada esta em" in texto.lower() or texto.lower().startswith("pronto"):
        return (f"Pronto, marquei {alvo} e ja abri a imagem aqui pra voce ver.")
    return texto or "Marquei, mas nao consegui confirmar a imagem."
