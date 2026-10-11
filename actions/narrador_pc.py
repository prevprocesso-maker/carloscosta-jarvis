# -*- coding: utf-8 -*-
"""A VOZ enquanto o JARVIS usa o computador sozinho.

Por que existe (03/09/2026)
---------------------------
Uma tarefa na tela leva de 30 segundos a varios minutos. Nesse tempo o mouse
anda sozinho e a tela pisca. Sem ninguem falando, o dono acha que travou, mexe
no mouse e ESTRAGA o passo (o clique cai no lugar errado porque a tela mudou).

Entao o JARVIS narra: avisa no comeco, repete de vez em quando com frase DIFERENTE
(a mesma frase repetida soa a robo travado) e conta o que esta fazendo quando o
Codex manda um marco ("abrindo o YouTube", "achei o campo de pesquisa").

Regras que a voz respeita:
- primeira fala imediata e completa: o que vai fazer, quanto pode demorar, que
  nao e para mexer no mouse nem no teclado, e como parar;
- depois, uma frase a cada `INTERVALO_S` — nunca a mesma duas vezes seguidas;
- o texto muda conforme o tempo passa (comeco / meio / finalizando);
- marco do Codex tem prioridade e reinicia o relogio (nao falar duas coisas
  coladas);
- silencio total enquanto ha pergunta pendente ao dono, senao a voz atropela a
  resposta dele.
"""
from __future__ import annotations

import random
import threading
import time

# ── AS DUAS REGRAS, E ELAS SAO DIFERENTES POR CLIENTE ─────────────────────
#
# TJ Mecanica (Bob, 03/09/2026): "avisa no comeco, mas no MEIO nao fala mais
# nada" — ele grava e atende enquanto o assistente trabalha, e voz no meio
# atrapalha. La o `FALAR_DURANTE` fica desligado.
#
# AJ Consultoria (JARVIS, 04/09/2026): o oposto, e faz sentido — ele NAO fica
# olhando a tela. Pediu que o JARVIS conte o andamento **de dois em dois minutos**
# ("como esta, se esta finalizando, nao mexa no mouse nem no teclado").
# Por isso, aqui: ligado, a cada 120 s.
FALAR_DURANTE = True

# MARCO e outra coisa: e a fala curta que o Codex manda a CADA passo ("abrindo
# personalizacao"). Passo custa ~6 s, entao marco falando vira tagarelice — o
# pedido foi de 2 em 2 minutos, nao de 6 em 6 segundos. Fica desligado; o
# lembrete do relogio, abaixo, e quem fala.
FALAR_MARCOS = False

INTERVALO_S = 120.0         # de quanto em quanto tempo ele volta a falar
MINIMO_ENTRE_FALAS_S = 12.0  # nem marco nem lembrete falam colados

# UMA FRASE SO' DE ABERTURA (22/09/2026, noite). Eram duas, sorteadas. Agora o
# aviso sai GRAVADO do disco (`core/fala_gravada.py`) — inteiro e na hora, em
# vez de vir da internet aos pedacos, que era o "fala, corta, fala" que o Diego
# ouvia. Frase gravada tem de ser sempre a mesma; o sorteio so' faria metade
# das tarefas cair de novo no caminho picotado.
ABERTURA_PC = (
    "Certo, chefe. Vou usar o computador agora. Nao mexa no mouse nem no teclado ate eu terminar. "
    "Isso pode levar alguns minutos. Se quiser que eu pare na hora, aperte a tecla ESC.")

_ABERTURA = [ABERTURA_PC]

_CEDO = [
    "Continuo aqui, chefe. Nao mexa no mouse nem no teclado, por favor.",
    "Ainda estou trabalhando na tela. Deixa o mouse quieto que eu resolvo.",
    "Estou no meio do caminho. Nao encoste no teclado agora.",
    "Seguindo com a tarefa. Nao precisa fazer nada, so nao mexer no mouse.",
]

_MEIO = [
    "Aguenta mais um pouco, chefe. Ainda estou trabalhando.",
    "Continuo na tarefa. Nao mexa no mouse nem no teclado, por favor.",
    "Ainda estou aqui, tudo caminhando. Se quiser parar, aperte ESC.",
    "Sigo trabalhando na tela. Nao encoste no computador agora.",
]

# SO FALA EM "FINALIZANDO" QUANDO REALMENTE ESTIVER PERTO DO FIM. O laco tem
# teto de 60 passos e 15 minutos; a partir de uns dois tercos do caminho a
# frase e verdadeira. Antes disso ela seria promessa — e promessa quebrada no
# meio de uma tarefa longa e pior do que silencio.
_FINALIZANDO = [
    "Aguarde mais um pouco, estou finalizando.",
    "Estou terminando agora, chefe. So mais um instante.",
    "Ultimos passos. Ja ja eu te aviso. Nao mexa no mouse.",
    "Estou fechando a tarefa. Um instante e eu falo com voce.",
]


# ── O DESENHO TEM O SEU PROPRIO TEXTO (07/09/2026) ────────────────────────
#
# Queixa do Diego depois do primeiro desenho de verdade: *"quando comecou ele
# apenas falou 'vou desenhar', mas nao falou que nao e pra mexer no teclado ou
# mouse, e quando terminou nao avisou que terminou e que o computador esta
# livre"*. E o mesmo cuidado do computer use — e pela mesma razao: o mouse anda
# sozinho por mais de um minuto, e se ele encostar o traco sai torto.
#
# Texto proprio porque as frases de cima falam em "tarefa" e "passos"; aqui o
# dono esta VENDO o desenho nascer, e o que ele precisa saber e quanto falta.
# O AVISO SAI NA HORA EM QUE ELE PEDE, e nao quando o desenho comeca
# (07/09/2026, 2a queixa do Diego): *"ele apenas fala que vai desenhar, fica
# quase 1 min sem fazer nada, depois ele fala nao mexa no teclado nem mouse e
# inicia"*. Montar o plano leva de 20 a 40 s — meio minuto em que ele nao sabia
# se devia ou nao encostar no mouse. Agora o recado completo e o PRIMEIRO que
# ele ouve, e ja fala do tempo de pensar.
ABERTURA_DESENHO = [
    "Deixa comigo, chefe. Vou desenhar {assunto} no Paint. Primeiro eu monto o "
    "desenho na cabeca, leva uns segundos, e depois desenho na tela. De agora "
    "ate eu terminar, nao mexa no mouse nem no teclado. Se quiser que eu pare, "
    "aperte ESC ou diga para parar.",
    "Pode deixar, chefe. Vou desenhar {assunto} no Paint. Comeco montando o "
    "desenho, uns segundos, e ja passo para a tela. Nao encoste no mouse nem "
    "no teclado ate eu terminar, senao o traco sai torto. Para parar na hora, "
    "aperte ESC.",
]

MEIO_DESENHO = [
    "Ainda desenhando, chefe. Nao mexa no mouse, por favor.",
    "Continuo desenhando. Deixa o mouse quieto que ja esta ficando bom.",
    "Sigo no desenho. Falta pouco — nao encoste no teclado agora.",
]

FIM_DESENHO = " Terminei. O computador e seu de novo."

# ── AS MESMAS FALAS EM INGLES (24/09/2026) ────────────────────────────────
# Cliente no Canada. Ver `core/idioma.py`. O aviso de abertura continua UMA
# frase so' (ela e gravada em disco uma vez por voz).
try:
    from core.idioma import em_ingles as _em_ingles
except Exception:      # pragma: no cover
    def _em_ingles():
        return False

if _em_ingles():
    ABERTURA_PC = (
        "Alright, boss. I'm going to use the computer now. Please don't touch the mouse "
        "or the keyboard until I'm done. This may take a few minutes. If you want me to "
        "stop right away, press the ESC key.")
    _ABERTURA = [ABERTURA_PC]
    _CEDO = [
        "Still here, boss. Please don't touch the mouse or the keyboard.",
        "I'm still working on the screen. Leave the mouse alone and I'll handle it.",
        "I'm halfway there. Please don't touch the keyboard right now.",
        "Moving on with the task. You don't need to do anything, just leave the mouse alone.",
    ]
    _MEIO = [
        "Hang on a little longer, boss. I'm still working.",
        "Still on the task. Please don't touch the mouse or the keyboard.",
        "Still here, everything is moving along. If you want to stop, press ESC.",
        "Still working on the screen. Please don't touch the computer right now.",
    ]
    _FINALIZANDO = [
        "Just a little longer, I'm finishing up.",
        "I'm wrapping up now, boss. Just one more moment.",
        "Last steps. I'll let you know shortly. Don't touch the mouse.",
        "I'm closing out the task. One moment and I'll talk to you.",
    ]
    ABERTURA_DESENHO = [
        "Leave it to me, boss. I'm going to draw {assunto}. First I plan the drawing, "
        "it takes a few seconds, and then I draw it on the screen. From now until I'm "
        "done, don't touch the mouse or the keyboard. If you want me to stop, press ESC "
        "or tell me to stop.",
    ]
    MEIO_DESENHO = [
        "Still drawing, boss. Please don't touch the mouse.",
        "Still drawing. Leave the mouse alone, it's looking good.",
        "Almost there with the drawing. Don't touch the keyboard right now.",
    ]
    FIM_DESENHO = " I'm done. The computer is yours again."


class Narrador:
    """Fala de tempos em tempos numa thread propria, ate mandarem parar."""

    def __init__(self, falar, pausado=None, intervalo_s: float = INTERVALO_S,
                 aberturas=None, lembretes=None, falar_aviso=None):
        # `aberturas`/`lembretes`: quem chama pode trazer o proprio texto (o
        # desenho traz). Sem eles, valem as frases do computer use.
        #
        # `falar_aviso`: so para a ABERTURA. E a fala que nao pode faltar — e
        # o aviso de tirar a mao do mouse. Em 08/09/2026 a voz engasgou e o
        # dono nao ouviu nada: a tarefa comecou em silencio. Os lembretes de
        # 2 em 2 minutos continuam na fala normal; perder um deles nao custa.
        self._aberturas = list(aberturas) if aberturas else None
        self._lembretes = list(lembretes) if lembretes else None
        self._falar = falar
        self._falar_aviso = falar_aviso or falar
        self._pausado = pausado or (lambda: False)
        self._intervalo = float(intervalo_s)
        self._thread = None
        self._parar = threading.Event()
        self._ultima_fala_em = 0.0
        self._ultimo_texto = ""
        self._t0 = 0.0
        self._trava = threading.Lock()
        # Em que passo o laco esta, e de quantos. O lembrete de 2 em 2 minutos
        # fica mais util com isso ("estou no passo 7"): o dono ouve que a coisa
        # ANDOU, e nao so que ela continua. Quem atualiza e o proprio laco.
        self._passo = 0
        self._de_quantos = 0
        self._ultima_base = ""

    # ── ciclo de vida ──────────────────────────────────────────────────
    def comecar(self, falar_abertura: bool = True) -> None:
        # `falar_abertura=False`: quem chamou JA avisou o dono e so quer os
        # lembretes. E o caso do desenho — la o aviso tem de sair na HORA em
        # que o dono pede, nao meio minuto depois, quando o plano fica pronto.
        if self._thread is not None:
            return
        self._t0 = time.monotonic()
        self._parar.clear()
        if falar_abertura:
            self._dizer(random.choice(self._aberturas or _ABERTURA),
                        aviso=True)
        else:
            self._ultima_fala_em = time.monotonic()
        if not FALAR_DURANTE:
            return          # so o aviso de abertura; o fim quem fala e o laco
        self._thread = threading.Thread(target=self._laco, daemon=True)
        self._thread.start()

    def parar(self) -> None:
        self._parar.set()
        self._thread = None

    # ── o que o laco de passos manda para ca ───────────────────────────
    def andamento(self, passo: int, de_quantos: int = 0) -> None:
        """O laco avisa em que passo esta. Nao fala nada — so anota."""
        self._passo = int(passo or 0)
        if de_quantos:
            self._de_quantos = int(de_quantos)

    def marco(self, texto: str) -> None:
        """Frase curta vinda do Codex ("abrindo o YouTube"). Nao e obrigatoria."""
        if not (FALAR_DURANTE and FALAR_MARCOS):
            return
        t = (texto or "").strip()
        if not t or len(t) > 140:
            return
        if time.monotonic() - self._ultima_fala_em < MINIMO_ENTRE_FALAS_S:
            return
        self._dizer(t)

    # ── por dentro ─────────────────────────────────────────────────────
    def _pool(self) -> list[str]:
        # As faixas acompanham o INTERVALO. Com lembrete de 2 em 2 minutos, o
        # primeiro cai aos 120 s: se "finalizando" comecasse aos 180 s, quase
        # toda tarefa ouviria "estou terminando" no segundo aviso — e a maioria
        # ainda estaria no meio. Agora "finalizando" so entra perto do teto
        # real do laco (15 min), ou quando o passo ja passou de dois tercos.
        if self._lembretes:
            return self._lembretes
        decorrido = time.monotonic() - self._t0
        perto_do_fim = (self._de_quantos and self._passo >= self._de_quantos * 0.66)
        if perto_do_fim or decorrido > 540:
            return _FINALIZANDO
        if decorrido < 150:
            return _CEDO
        return _MEIO

    def _escolher(self, pool: list[str]) -> str:
        # Compara pela frase BASE, nao pelo texto ja falado: desde que o passo
        # entra no fim ("... Estou no passo 7."), o texto nunca se repete
        # literalmente, e a regra de "nao dizer a mesma coisa duas vezes
        # seguidas" — que existe porque frase repetida soa a robo travado —
        # deixaria de valer sem ninguem perceber.
        op = [f for f in pool if f != self._ultima_base] or list(pool)
        frase = random.choice(op)
        self._ultima_base = frase
        # "Estou no passo 7" so entra quando ha passo de verdade para contar.
        if self._passo >= 2:
            frase = (f"{frase} I'm on step {self._passo}." if _em_ingles()
                     else f"{frase} Estou no passo {self._passo}.")
        return frase

    def _dizer(self, texto: str, aviso: bool = False) -> None:
        with self._trava:
            self._ultimo_texto = texto
            self._ultima_fala_em = time.monotonic()
        try:
            (self._falar_aviso if aviso else self._falar)(texto)
        except Exception:
            pass

    def _laco(self) -> None:
        while not self._parar.is_set():
            # Dorme em fatias para responder rapido ao "parar".
            alvo = self._ultima_fala_em + self._intervalo
            while not self._parar.is_set() and time.monotonic() < alvo:
                time.sleep(0.25)
                alvo = self._ultima_fala_em + self._intervalo
            if self._parar.is_set():
                return
            if self._pausado():          # pergunta pendente: boca fechada
                self._ultima_fala_em = time.monotonic()
                continue
            self._dizer(self._escolher(self._pool()))
