# -*- coding: utf-8 -*-
"""Diario das exclusoes e o caminho de volta pela Lixeira do Windows.

Por que existe (01/09/2026)
---------------------------
O JARVIS ja excluia mandando para a Lixeira (send2trash), mas "desfazer" nao
existia: o dono tinha de abrir a Lixeira e procurar. Agora cada exclusao
confirmada fica anotada aqui e "pode desfazer" restaura o item pelo proprio
Windows (Shell.Application, o mesmo botao "Restaurar" da Lixeira), de volta ao
lugar de onde saiu.

Como o item e achado na Lixeira: pela pasta de origem (coluna "Local original")
e pelo nome. A Lixeira guarda o item com outro nome interno ($R...), entao a
extensao real vem de `item.Path`, nao do nome exibido — o Explorer pode
esconder a extensao no nome.

COM fora da thread principal exige CoInitialize; sem isso o Dispatch estoura.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
DIARIO = BASE / "config" / "exclusoes.json"


def _ler() -> list:
    try:
        d = json.loads(DIARIO.read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except Exception:
        return []


def _gravar(itens: list) -> None:
    DIARIO.parent.mkdir(parents=True, exist_ok=True)
    DIARIO.write_text(json.dumps(itens[-200:], ensure_ascii=False, indent=1), encoding="utf-8")


def registrar_exclusao(caminho) -> None:
    """Chamado logo depois de mandar para a Lixeira. Nunca levanta erro."""
    try:
        itens = _ler()
        itens.append({"caminho": str(caminho), "quando": time.time(), "restaurado": False})
        _gravar(itens)
    except Exception:
        pass


def ultima_exclusao_em() -> float:
    """Quando foi a ultima exclusao ainda nao desfeita (epoch), ou 0."""
    for it in reversed(_ler()):
        if not it.get("restaurado"):
            return float(it.get("quando") or 0)
    return 0.0


def _restaurar_pela_lixeira(caminho: Path) -> bool:
    import pythoncom  # noqa: PLC0415
    import win32com.client  # noqa: PLC0415
    pythoncom.CoInitialize()
    try:
        shell = win32com.client.Dispatch("Shell.Application")
        lixeira = shell.NameSpace(10)          # ssfBITBUCKET
        if lixeira is None:
            return False
        pai = str(caminho.parent).rstrip("\\").lower()
        nome = caminho.name.lower()
        raiz = caminho.stem.lower()
        ext = caminho.suffix.lower()
        for item in lixeira.Items():
            origem = str(lixeira.GetDetailsOf(item, 1) or "").rstrip("\\").lower()
            if origem != pai:
                continue
            exibido = str(item.Name or "").lower()
            ext_real = Path(str(item.Path or "")).suffix.lower()
            if not (exibido == nome or (exibido == raiz and ext_real == ext) or exibido + ext_real == nome):
                continue
            for verbo in item.Verbs():
                vn = str(verbo.Name or "").replace("&", "").lower()
                if "restaur" in vn or "restore" in vn:
                    verbo.DoIt()
                    # O Windows restaura em segundo plano; da uns segundos.
                    for _ in range(30):
                        if caminho.exists():
                            return True
                        time.sleep(0.1)
                    return caminho.exists()
        return False
    finally:
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass


def restaurar_ultima() -> str:
    """Traz de volta o ultimo item excluido pelo JARVIS. Texto para falar."""
    itens = _ler()
    for i in range(len(itens) - 1, -1, -1):
        it = itens[i]
        if it.get("restaurado"):
            continue
        caminho = Path(str(it.get("caminho") or ""))
        if not str(caminho):
            continue
        if caminho.exists():
            it["restaurado"] = True
            _gravar(itens)
            return f"{caminho.name} ja esta de volta em {caminho.parent.name}."
        try:
            ok = _restaurar_pela_lixeira(caminho)
        except Exception as exc:
            return f"Nao consegui abrir a Lixeira para restaurar: {str(exc)[:80]}"
        if ok:
            it["restaurado"] = True
            _gravar(itens)
            return f"Restaurado. {caminho.name} voltou para {caminho.parent.name}."
        it["restaurado"] = True          # nao insistir num item que sumiu da Lixeira
        _gravar(itens)
        return (f"Nao achei {caminho.name} na Lixeira — pode ter sido esvaziada "
                f"ou o arquivo ja foi restaurado por voce.")
    return "Nao tenho nenhuma exclusao para desfazer."
