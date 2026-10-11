# -*- coding: utf-8 -*-
"""Falas FIXAS guardadas em disco, ja inteiras.

Por que existe (22/09/2026, noite)
----------------------------------
Queixa do Diego com a v1.0.28: *"quando eu ativo computador e peco pra ele
fazer uma tarefa, quando ele comeca a falar para nao mexer no teclado essas
coisas ele fica falando cortando muito toda hora"*.

O registro das 21:13 mostra o mecanismo: o aviso de abertura (175 letras,
~9 s de fala) veio da sessao ao vivo da Gemini em pedacos, mais devagar que o
tempo real — 4,1 s de audio em 11 s — e parou no meio (`voz_cortada`). Tocando
conforme chega, cada buraco da rede vira um buraco na voz: "fala, para, fala".

O aviso e SEMPRE a mesma frase. Nao ha motivo para pedir essa frase a
internet toda vez e torcer para ela vir inteira. Ela e gerada UMA vez por voz,
conferida (tem de ter o tamanho de uma fala completa) e guardada aqui. Dali em
diante sai do disco: na hora, inteira, sem rede.

A chave junta tudo o que muda o som: o motor, o personagem, o timbre, o jeito
e a velocidade. Trocar qualquer um deles na engrenagem gera um arquivo novo,
nunca toca o som antigo.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

PASTA = Path(__file__).resolve().parents[1] / "cache" / "falas"


def _arquivo(chave: str, texto: str) -> Path:
    bruto = f"{chave}|{str(texto or '').strip()}".encode("utf-8")
    return PASTA / (hashlib.sha1(bruto).hexdigest()[:24] + ".pcm")


def ler(chave: str, texto: str) -> bytes | None:
    """O audio guardado (PCM 16 bits, 24 kHz, mono) ou None."""
    try:
        caminho = _arquivo(chave, texto)
        if not caminho.is_file():
            return None
        raw = caminho.read_bytes()
        # Menos de meio segundo nao e fala: arquivo estragado, finge que nao ha.
        if len(raw) < 24000:
            return None
        return raw[: len(raw) - (len(raw) % 2)]
    except Exception:
        return None


def gravar(chave: str, texto: str, raw: bytes) -> bool:
    """Guarda em dois tempos (temporario + troca) para nunca deixar meio arquivo."""
    try:
        if not raw or len(raw) < 24000:
            return False
        PASTA.mkdir(parents=True, exist_ok=True)
        caminho = _arquivo(chave, texto)
        temp = caminho.with_suffix(".tmp")
        temp.write_bytes(bytes(raw[: len(raw) - (len(raw) % 2)]))
        os.replace(temp, caminho)
        return True
    except Exception:
        return False
