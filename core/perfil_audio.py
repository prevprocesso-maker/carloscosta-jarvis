# -*- coding: utf-8 -*-
"""O OUVIDO QUE SE ADAPTA A CADA MICROFONE (01/10/2026).

Pedido do Diego: "tem coisas que ela nao me entende, assim como outros clientes
que usam outros PCs, outros microfones... nao teria como o JARVIS reconhecer cada
PC, cada microfone, e se adaptar para escutar certo em cada um?"

O defeito, visto no registro do proprio PC dele
------------------------------------------------
Depois de gravar, o JARVIS decide pelo PICO do audio o que e fala e o que e
barulho, com dois numeros FIXOS medidos no microfone do Diego em setembro:

    abaixo de 0,045 ......... descarta sem ouvir ("som_de_longe")
    0,045 a 0,09 ............ ouve, mas so aceita com confianca alta ("resgate")
    acima de 0,09 ........... fala normal

Os dois erram para os dois lados, dependendo do aparelho:
  * microfone fraco (cliente com headset longe da boca): a voz inteira mora entre
    0,03 e 0,08 — vira "resgate" ou descarte, e o JARVIS "nao entende";
  * sala barulhenta (o Diego em 01/10, video tocando): o barulho bate 0,05 a 0,11,
    passa por fala, e o ouvido inventa "E assim ficou pronto", "Obrigado",
    "Caze TV" — que os filtros jogam fora, mas o ouvido fica ocupado com isso.
3.731 trechos foram descartados so pelo pico no PC do Diego.

O que este arquivo faz
----------------------
Guarda, POR MICROFONE, o pico das falas que deram certo e o pico do barulho que
o ouvido confirmou ser barulho (descartado por confianca, alucinacao, eco). Com
essas duas listas os limites passam a ser DESTE aparelho:

    resgate = barulho tipico x 1,1     (sem passar de 60% da fala mais baixa)
    piso    = fala mais baixa x 0,85   (e acima do barulho mais alto, quando da)

Comeca com o resgate mais baixo que o antigo (0,025) para conseguir APRENDER a
voz baixa; em uns poucos minutos de uso os numeros ja sao do aparelho. Fica salvo
em `config/perfil_audio.json`, uma entrada por microfone: trocar de microfone e
comecar outro perfil, sem estragar o do anterior.

So o que o ouvido CONFIRMOU entra na conta. O que foi descartado so pelo pico
nunca vira "barulho" — senao uma voz baixa descartada empurraria o limite para
cima e o aparelho nunca aprenderia a ouvi-la.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

_RAIZ = Path(__file__).resolve().parents[1]
_ARQUIVO = _RAIZ / "config" / "perfil_audio.json"

# Os numeros de antes, para o aparelho que ainda nao tem historia.
PISO_PADRAO = 0.09
RESGATE_PADRAO = 0.045
# Aparelho novo: resgate mais baixo, para dar ao ouvido a chance de CONFIRMAR
# o que e voz baixa e o que e barulho. Barulho confirmado sobe o numero sozinho.
RESGATE_INICIAL = 0.025

_MINIMO_RESGATE = 0.012     # abaixo disso e chiado eletrico, nao ha voz
# 0,06 e nao mais: acima disso uma voz que de vez em quando sai mais baixa (o
# dono longe do microfone) nunca mais seria ouvida para o perfil aprender com ela.
_MAXIMO_RESGATE = 0.06
_MAXIMO_PISO = 0.30
_AMOSTRAS = 6               # quantas de cada tipo antes de confiar na conta
_GUARDA_FALA = 80
_GUARDA_RUIDO = 120

_trava = threading.Lock()


def _percentil(valores: list, p: float) -> float:
    v = sorted(valores)
    if not v:
        return 0.0
    k = (len(v) - 1) * p / 100.0
    a = int(k)
    b = min(a + 1, len(v) - 1)
    return v[a] + (v[b] - v[a]) * (k - a)


def _anotar_diario(evento: str, detalhe: str) -> None:
    try:
        from datetime import datetime as _dt  # noqa: PLC0415
        caminho = _RAIZ / "logs" / "voice_session.log"
        caminho.parent.mkdir(parents=True, exist_ok=True)
        with open(caminho, "a", encoding="utf-8") as f:
            f.write(f"{_dt.now().isoformat(timespec='milliseconds')}\t{evento}\t{detalhe}\n")
    except Exception:
        pass


class PerfilDoMicrofone:
    """Os limites de fala e barulho de UM microfone, aprendidos no uso."""

    def __init__(self, chave: str) -> None:
        self.chave = str(chave or "padrao")[:120]
        self.fala: list = []
        self.ruido: list = []
        self._ultima = {"fala": 0.0, "ruido": 0.0}
        self._sujo = 0
        self._ultimo_resumo = ""
        try:
            dados = json.loads(_ARQUIVO.read_text(encoding="utf-8"))
            meu = (dados.get("microfones") or {}).get(self.chave) or {}
            self.fala = [float(x) for x in (meu.get("fala") or [])][-_GUARDA_FALA:]
            self.ruido = [float(x) for x in (meu.get("ruido") or [])][-_GUARDA_RUIDO:]
        except Exception:
            pass

    # ── os limites ──────────────────────────────────────────────────────
    def limites(self) -> tuple:
        """(resgate, piso) deste microfone agora."""
        fala, ruido = self.fala[-_GUARDA_FALA:], self.ruido[-_GUARDA_RUIDO:]
        conhece_fala = len(fala) >= _AMOSTRAS
        conhece_ruido = len(ruido) >= _AMOSTRAS
        fala_baixa = _percentil(fala, 10) if conhece_fala else 0.0
        fala_media = _percentil(fala, 50) if conhece_fala else 0.0

        # RESGATE: logo acima do barulho tipico — sem nunca encostar na voz.
        if conhece_ruido:
            resgate = _percentil(ruido, 50) * 1.1
        elif conhece_fala:
            resgate = RESGATE_PADRAO
        else:
            resgate = RESGATE_INICIAL
        if conhece_fala:
            resgate = min(resgate, fala_baixa * 0.6)
        resgate = max(_MINIMO_RESGATE, min(_MAXIMO_RESGATE, resgate))

        # PISO: a fala mais baixa DESTE aparelho manda.
        piso = fala_baixa * 0.85 if conhece_fala else PISO_PADRAO
        if conhece_ruido:
            ruido_alto = _percentil(ruido, 90) * 1.1
            # Barulho alto empurra o piso para cima, para nao passar por fala
            # normal — a nao ser que ele ja esteja no meio das falas reais
            # (sala muito barulhenta): ai quem separa e a confianca do ouvido.
            if not conhece_fala or ruido_alto < fala_media:
                piso = max(piso, ruido_alto)
        piso = max(resgate * 1.3, min(_MAXIMO_PISO, piso))
        return resgate, piso

    def resumo(self) -> str:
        resgate, piso = self.limites()
        return (f"{self.chave[:40]} | resgate {resgate:.3f} piso {piso:.3f} | "
                f"{len(self.fala)} falas, {len(self.ruido)} barulhos aprendidos")

    # ── o aprendizado ───────────────────────────────────────────────────
    def anotar(self, tipo: str, pico: float) -> None:
        """Uma fala aceita ('fala') ou um barulho confirmado ('ruido')."""
        if tipo not in ("fala", "ruido") or not (0.0 < float(pico or 0) <= 1.0):
            return
        agora = time.monotonic()
        with _trava:
            # A espiada adiantada ouve a mesma fala varias vezes enquanto ela
            # cresce: so a primeira de cada 1,5 s conta.
            if agora - self._ultima[tipo] < 1.5:
                return
            self._ultima[tipo] = agora
            lista = self.fala if tipo == "fala" else self.ruido
            lista.append(round(float(pico), 4))
            del lista[:-(_GUARDA_FALA if tipo == "fala" else _GUARDA_RUIDO)]
            self._sujo += 1
            precisa_salvar = self._sujo >= 3
        if precisa_salvar:
            self.salvar()
        novo = self.resumo()
        # O registro so ganha linha quando o limite MUDA de verdade.
        if novo.split("|")[1] != (self._ultimo_resumo.split("|")[1] if self._ultimo_resumo else ""):
            self._ultimo_resumo = novo
            _anotar_diario("perfil_audio", novo)

    def salvar(self) -> None:
        with _trava:
            self._sujo = 0
            try:
                try:
                    dados = json.loads(_ARQUIVO.read_text(encoding="utf-8"))
                except Exception:
                    dados = {}
                mics = dados.setdefault("microfones", {})
                mics[self.chave] = {"fala": self.fala[-_GUARDA_FALA:],
                                    "ruido": self.ruido[-_GUARDA_RUIDO:],
                                    "atualizado": time.strftime("%Y-%m-%d %H:%M")}
                _ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
                tmp = _ARQUIVO.with_suffix(".tmp")
                tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
                tmp.replace(_ARQUIVO)
            except Exception as exc:
                _anotar_diario("perfil_audio_falhou", str(exc)[:100])


_atual: PerfilDoMicrofone | None = None


def perfil(chave: str | None = None) -> PerfilDoMicrofone:
    """O perfil do microfone em uso. Com `chave` nova, troca de perfil."""
    global _atual
    if chave is not None and (_atual is None or _atual.chave != str(chave)[:120]):
        if _atual is not None:
            _atual.salvar()
        _atual = PerfilDoMicrofone(chave)
        _anotar_diario("perfil_audio", "microfone em uso: " + _atual.resumo())
    if _atual is None:
        _atual = PerfilDoMicrofone("padrao")
    return _atual
