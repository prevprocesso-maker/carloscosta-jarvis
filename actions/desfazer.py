# -*- coding: utf-8 -*-
"""'Pode desfazer': volta a ULTIMA coisa que o JARVIS fez, seja qual for.

Hoje duas acoes tem volta: organizar uma pasta (`organizar_pasta`, diario em
config/organizacoes) e excluir arquivo ou pasta (`lixeira`, diario em
config/exclusoes.json). Aqui se escolhe a mais recente das duas e se desfaz.
Quem quiser dar volta a uma acao nova so precisa de um diario com data e
entrar na lista de `ultima_acao`.
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

JANELA_S = 15 * 60      # "desfaz" sem dizer o que so vale por 15 minutos


def _ultima_organizacao_em() -> float:
    from actions.organizar_pasta import _diarios_de  # noqa: PLC0415
    diarios = _diarios_de(None)
    if not diarios:
        return 0.0
    try:
        d = json.loads(diarios[0].read_text(encoding="utf-8"))
        return datetime.strptime(d["quando"], "%Y%m%d-%H%M%S").timestamp()
    except Exception:
        return 0.0


def ultima_acao() -> tuple[str, float] | None:
    """('organizar' | 'excluir', epoch) da acao mais recente com volta, ou None."""
    from actions.lixeira import ultima_exclusao_em  # noqa: PLC0415
    candidatos = [("organizar", _ultima_organizacao_em()), ("excluir", ultima_exclusao_em())]
    candidatos = [c for c in candidatos if c[1] > 0]
    if not candidatos:
        return None
    return max(candidatos, key=lambda c: c[1])


def ha_algo_recente() -> bool:
    u = ultima_acao()
    return bool(u) and (time.time() - u[1]) < JANELA_S


def desfazer_ultima() -> str:
    u = ultima_acao()
    if not u:
        return "Nao tenho nada recente para desfazer."
    tipo, _ = u
    if tipo == "organizar":
        from actions.organizar_pasta import desfazer  # noqa: PLC0415
        return desfazer(None)
    from actions.lixeira import restaurar_ultima  # noqa: PLC0415
    return restaurar_ultima()


def desfazer_tool(parameters: dict | None = None, player=None, **_ignorado) -> str:
    r = desfazer_ultima()
    try:
        if player is not None and hasattr(player, "write_log"):
            player.write_log(f"[desfazer] {r}")
    except Exception:
        pass
    return r
