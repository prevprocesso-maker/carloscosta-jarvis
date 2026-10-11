# -*- coding: utf-8 -*-
"""Cofre de acessos do dono — usuario e senha dos sistemas que o JARVIS opera.

Por que existe (03/09/2026)
---------------------------
Para o Bob entrar sozinho no sistema da oficina, alguem tem de digitar a senha.
Tres coisas nao podem acontecer:

  1. a senha ir para a VPS ou para o modelo (ela nao sai deste computador);
  2. a senha ficar em texto puro num .json na area de trabalho;
  3. o Bob inventar senha ("tenta admin/123") — se nao tem, ele PERGUNTA.

Entao: o valor e cifrado pelo proprio Windows (DPAPI, `CryptProtectData`), que
amarra o segredo a CONTA de usuario desta maquina. O arquivo copiado para outro
PC vira lixo ilegivel — nem o mesmo Windows de outro usuario abre. E o Codex,
la na VPS, so ve o APELIDO ("sistema da oficina"), nunca o valor: ele responde
{"acao":"credencial","apelido":"oficina","campo":"senha"} e quem digita e o PC.

Onde fica: `config/acessos.json`, ao lado das outras configuracoes do Bob. Nao
entra em backup nenhum de proposito (ver `_empacotar.py`).
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

_ARQUIVO = Path(__file__).resolve().parent.parent / "config" / "acessos.json"
_CAMPOS = ("usuario", "senha", "url", "observacao")


def _cifrar(texto: str) -> str:
    import win32crypt  # noqa: PLC0415
    bruto = win32crypt.CryptProtectData(texto.encode("utf-8"), "JARVIS CyberBot",
                                        None, None, None, 0)
    return base64.b64encode(bruto).decode("ascii")


def _decifrar(guardado: str) -> str:
    import win32crypt  # noqa: PLC0415
    bruto = base64.b64decode(guardado.encode("ascii"))
    _desc, texto = win32crypt.CryptUnprotectData(bruto, None, None, None, 0)
    return texto.decode("utf-8", "replace")


def _ler_arquivo() -> dict:
    try:
        return json.loads(_ARQUIVO.read_text("utf-8"))
    except Exception:
        return {}


def _gravar_arquivo(dados: dict) -> None:
    _ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
    _ARQUIVO.write_text(json.dumps(dados, ensure_ascii=False, indent=2), "utf-8")


def _chave(apelido: str) -> str:
    return (apelido or "").strip().lower()[:60]


def salvar_credencial(apelido: str, campo: str, valor: str) -> bool:
    """Guarda um dado de acesso, cifrado. `campo`: usuario, senha, url..."""
    campo = (campo or "").strip().lower()
    if campo not in _CAMPOS or not _chave(apelido) or not valor:
        return False
    dados = _ler_arquivo()
    entrada = dados.get(_chave(apelido)) or {}
    # url e observacao nao sao segredo: ficam legiveis para o dono conferir.
    entrada[campo] = valor if campo in ("url", "observacao") else {"protegido": _cifrar(valor)}
    dados[_chave(apelido)] = entrada
    _gravar_arquivo(dados)
    return True


def ler_credencial(apelido: str, campo: str = "senha") -> str | None:
    """Devolve o valor em texto — SO para digitar aqui na maquina."""
    entrada = _ler_arquivo().get(_chave(apelido))
    if not entrada:
        # "sistema da oficina" tem de achar a entrada "oficina".
        alvo = _chave(apelido)
        for k, v in _ler_arquivo().items():
            if k and (k in alvo or alvo in k):
                entrada = v
                break
    if not entrada:
        return None
    valor = entrada.get((campo or "").strip().lower())
    if isinstance(valor, dict) and valor.get("protegido"):
        try:
            return _decifrar(valor["protegido"])
        except Exception:
            return None
    return valor if isinstance(valor, str) else None


def listar() -> list[dict]:
    """O que existe no cofre, SEM valor nenhum de segredo."""
    saida = []
    for apelido, entrada in _ler_arquivo().items():
        saida.append({
            "apelido": apelido,
            "url": entrada.get("url") or "",
            "tem_usuario": bool(entrada.get("usuario")),
            "tem_senha": bool(entrada.get("senha")),
        })
    return saida


def esquecer(apelido: str) -> bool:
    dados = _ler_arquivo()
    if _chave(apelido) in dados:
        dados.pop(_chave(apelido))
        _gravar_arquivo(dados)
        return True
    return False
