# -*- coding: utf-8 -*-
"""O JARVIS usando o computador sozinho — olhar, decidir, clicar, conferir.

Como funciona
-------------
Um laco. A cada volta:

    1. OLHAR   `tela_visao.olhar()` espera a tela parar, tira a foto, pergunta
               ao Windows quais sao os elementos clicaveis e NUMERA cada um em
               cima da foto.
    2. DECIDIR a foto + a lista numerada + o que ja foi feito vao para a VPS,
               que mostra tudo ao Codex CLI. Ele responde ate 4 movimentos em
               JSON ("clica no elemento 12", "digita tal coisa", "aperta Enter").
    3. FAZER   este arquivo executa os movimentos aqui no PC (pyautogui,
               SendInput, win32) e anota o que aconteceu.
    4. CONFERIR a tela mudou? se nao mudou, o proximo pedido avisa o Codex, que
               tenta outro caminho em vez de repetir o mesmo clique.

O Codex nunca encosta no PC: ele so enxerga a foto e responde. Quem mexe e este
arquivo. A cabeca fica na VPS pelo plano do dono (sem cobranca por uso).

O QUE MUDOU NA v2 (03/09/2026) E POR QUE
----------------------------------------
- ELEMENTO EM VEZ DE PIXEL. Antes o Codex chutava x,y na foto reduzida; erro de
  15 px derruba um botao de 24 px. Agora ele responde o NUMERO do elemento e o
  clique cai no centro exato, em pixel real. Pixel continua valendo para o que
  nao tem elemento (video, imagem, area de desenho).
- ATE 4 MOVIMENTOS POR VOLTA. Cada ida a VPS custa ~5 s. "clicar no campo,
  digitar, Enter" era 15 s em tres voltas; agora e uma.
- ESPERAR A TELA PARAR antes de fotografar (pagina carregando enganava o Codex).
- ANTI-TEIMOSIA: mesmo movimento tres vezes sem a tela mudar vira aviso; seis
  voltas sem nada mudar encerra com explicacao, em vez de gastar o teto todo.
- NARRACAO: `narrador_pc` avisa o dono para nao mexer no mouse e vai contando,
  com frase diferente a cada vez.
- SENHA NUNCA VAI PARA A VPS: o Codex pede `credencial`, e quem digita e aqui,
  lendo do cofre local (`acessos.py`, protegido pelo Windows).

O QUE ENTROU NO JARVIS (04/09/2026) — a revisao pedida pelo Diego
--------------------------------------------------------------
O motor veio do Bob, aprovado pelo cliente da TJ. Aqui ele foi revisado inteiro
e ganhou o que faltava para MEXER EM ARQUIVO, que e o dia a dia da AJ (pasta,
subpasta, arrastar um arquivo para dentro de outra pasta):

- BOTAO DIREITO DE VERDADE: depois de `clicar_direito` a espera passou de 0,25 s
  para 0,45 s, senao a foto do proximo passo sai ANTES de o menu terminar de
  abrir e o Codex conclui que "nao aconteceu nada" (o menu do botao direito ja
  aparecia na lista de elementos — o que faltava era ele existir na hora certa).
- `modificador`: "ctrl", "shift" ou "alt" junto com o clique. E o que permite
  escolher VARIOS arquivos (ctrl+clique) ou um intervalo (shift+clique) antes de
  arrastar ou copiar. Sem isto, cada arquivo custava uma volta inteira na VPS.
- ARRASTAR PELO NOME: `arrastar` agora aceita elemento/nome nas DUAS pontas
  (`elemento2`/`nome2`), e o arrasto e feito em varios passos com pausa — o
  Explorer e o navegador ignoram um arrasto instantaneo, porque eles esperam ver
  o mouse ANDANDO para comecar a arrastar. E o "poe essa pasta dentro daquela".
- `abrir_pasta`: abre uma pasta (ou subpasta) direto pelo caminho, incluindo os
  nomes conhecidos ("downloads", "documentos", "area de trabalho"). Determinismo
  vale mais que procurar icone na tela — mesma ideia do `abrir_url`.
- ROLAR MAIS E PARA OS LADOS: `direcao` aceita cima/baixo/esquerda/direita,
  `quantidade` vai ate 40 e `ate_o_fim`/`ate_o_topo` levam ao fim da lista de
  uma vez (ctrl+End / ctrl+Home). "Desce mais" era o pedido que mais aparecia.

E os cinco defeitos que a revisao achou no motor do Bob (todos consertados
aqui; nenhum deles chegou a derrubar o teste da TJ, mas todos podem):

  1. A FAIXA NA TELA NUNCA APARECIA. O laco procura `mostrar_aviso_pc` no objeto
     que recebe (`JarvisUI`), e o metodo existia so na `MainWindow` la dentro —
     `getattr` devolvia None e o aviso sumia em silencio. Consertado no ui.py.
  2. Toda acao pagava uma varredura de tela a toa: `_alvo` rodava ANTES de saber
     o tipo do movimento, e para `clicar_nome`/`expandir`/`acionar`/`rolar_ate`
     (que procuram pelo nome por conta propria) isso era uma leitura completa da
     arvore de acessibilidade jogada fora — a mais cara da volta.
  3. `digitar` com alvo que NAO existe digitava assim mesmo, na janela em foco.
     O alvo era justamente a prova de que o Codex sabia onde escrever; sem ele,
     texto (ou senha) ia para o lugar errado. Agora e falha declarada.
  4. Tecla desconhecida respondia "apertei X" sem apertar nada — o pyautogui
     ignora nome que nao conhece. Mentira no historico faz o Codex seguir em
     frente achando que deu certo. Agora confere na lista dele antes.
  5. "contrabarra" mandava DUAS barras invertidas (escape a mais no dicionario),
     e numero vindo como "5.0" derrubava o passo inteiro no `int()`.

Freios (continuam valendo)
--------------------------
- "para/chega/cancela" durante o laco mata no proximo movimento;
- pagar, comprar, excluir, enviar, formatar... pergunta por voz antes;
- shift+delete (apagar SEM ir para a lixeira) tambem pergunta antes;
- ele so desiste quando PARA DE PROGREDIR (ver `MAX_PASSOS` abaixo);
- FAILSAFE do pyautogui: mouse no canto superior esquerdo aborta na hora.
"""
from __future__ import annotations

import ctypes
import json
import os
import random
import re
import secrets
import subprocess
import threading
import time

from actions import tela_visao
from actions.narrador_pc import Narrador

# ── O RELOGIO SAIU DA JOGADA (22/09/2026, pedido do Diego) ──────────────────
#
# Ele disse, com todas as letras: "ele tem um tempo; mesmo que o computer use
# nao termine a tarefa, chega num tempo que ele fala que o tempo esgotou. Pode
# tirar esse tempo, deixa ilimitado ate ele concluir."
#
# Antes: `TEMPO_MAXIMO_S = 900` e `MAX_PASSOS = 60`. Uma tarefa longa de
# verdade — desinstalar meia duzia de programas, varrer a lista inteira de
# notificacoes, esperar um `sfc /scannow` — estourava os 15 minutos NO MEIO e o
# dono ouvia "passei do tempo limite e parei". Trabalho jogado fora.
#
# Agora nao ha relogio nenhum. O que encerra a tarefa passa a ser o que sempre
# deveria: acabar, o dono mandar parar (voz ou ESC), ou PARAR DE PROGREDIR. Os
# freios de progresso ja existiam e continuam de pe — `SEM_MUDANCA_LIMITE`
# (tela que nao reage) e os avisos de movimento repetido.
#
# `MAX_PASSOS` continua existindo como trava de laco infinito, mas num numero
# alto o bastante para nunca ser o que corta uma tarefa de verdade: 60 viraram
# 600. A conta e simples — a tarefa mais longa que ja medimos aqui deu 42
# passos, e a que o Diego reclamou parou em 60.
MAX_PASSOS = 600
# Sem relogio. Fica `None` de proposito, e o laco nao tem mais `if passou do
# tempo`. Quem quiser um teto (um teste, por exemplo) passa `tempo_maximo_s`.
TEMPO_MAXIMO_S = None
# UM MOVIMENTO POR VEZ (22/09/2026, noite). Eram 4. Queixa do Diego olhando as
# notificacoes do Defender: *"ele tenta clicar, fazer de uma vez so tres,
# quatro, cinco cliques... ai depois desclica, ativa de novo, depois
# desativa. O ideal seria passo a passo: fez um passo, enviou para o Codex,
# o Codex enviou o proximo passo, ele reenvia a imagem novamente"*.
#
# O lote era montado sobre UMA foto: o primeiro clique desliga um interruptor,
# a pagina se mexe (aparece aviso, a lista desce), e os cliques seguintes caem
# em coordenadas de uma tela que ja nao existe — no interruptor vizinho, ou no
# mesmo de novo, religando. Agora cada movimento ganha foto nova antes do
# proximo. A unica dupla que continua junta e "digitar" + Enter/Tab: e a mesma
# entrada de texto, e a tela do meio nao decide nada. Ver `_um_movimento_por_vez`.
MAX_ACOES_POR_PASSO = 1
# Comandos de LEITURA seguidos antes de o programa cobrar acao. Oito da folga
# de sobra para um diagnostico honesto (ver servico, ler evento, achar
# arquivo); o que passa disso, no teste de 22/09/2026, foi sempre a mesma busca
# repetida com outras palavras.
TETO_DE_BUSCAS = 8
# Voltas seguidas sem a tela mudar antes de desistir. Seis porque tela parada
# tambem acontece de boa-fe (esperando pagina, campo ja preenchido).
SEM_MUDANCA_LIMITE = 6
ESPERA_MAXIMA_S = 15.0

# OS MOVIMENTOS QUE ESTE CLIENTE SABE FAZER ALEM DO BASICO (04/09/2026).
#
# Vao no `contexto` de cada passo e a VPS so ensina ao Codex o que estiver
# aqui. Cada nome tem de existir dos DOIS lados: aqui (executa) e no
# `ACOES_EXTRAS`/`DICAS_EXTRAS` do `jarvis-olhar.js` (oferece). Nome so de um
# lado nao quebra nada — simplesmente nunca e usado.
RECURSOS = ("menu_direito", "arrastar_para", "abrir_pasta", "rolar_mais",
            "responder", "comando", "fechar_erros",
            # 23/09/2026 (v1.0.29, VPS v3):
            #   abrir_config  o Win+R com o endereco — este cliente SEMPRE soube
            #                 fazer, mas nunca declarou, e a VPS descartava;
            #   um_por_vez    as regras do lado de la passam a dizer UM movimento;
            #   memoria       o caderno do modelo (vi/avaliacao/memoria) + regras
            #                 em campo proprio + historico so com o que e novo;
            #   ler_tela      o texto da janela, lido do Windows;
            #   ampliar       a proxima foto so com um pedaco, ampliado;
            #   janela        maximizar/minimizar/restaurar/metade/fechar.
            "abrir_config", "um_por_vez", "memoria", "ler_tela", "ampliar", "janela",
            "escolher")

# O REGISTRO DE CADA PASSO, EM ARQUIVO (23/09/2026). Ate aqui o passo a passo
# so aparecia no chat e sumia ao fechar: quando o cliente dizia "ele errou",
# nao havia como saber o que ele VIU nem o que decidiu. Agora cada passo deixa
# uma linha em logs\computer_use.log (o que o modelo viu, como avaliou o
# movimento anterior, o que mandou e o que aconteceu). Senha nunca entra: o
# `credencial` so registra "preenchi a senha".
_LOG_PASSOS = None
_LOG_PASSOS_TETO = 3 * 1024 * 1024


def _registrar_passo(texto: str) -> None:
    global _LOG_PASSOS
    try:
        if _LOG_PASSOS is None:
            from pathlib import Path as _P  # noqa: PLC0415
            pasta = _P(__file__).resolve().parents[1] / "logs"
            pasta.mkdir(parents=True, exist_ok=True)
            _LOG_PASSOS = pasta / "computer_use.log"
        if _LOG_PASSOS.exists() and _LOG_PASSOS.stat().st_size > _LOG_PASSOS_TETO:
            velho = _LOG_PASSOS.with_suffix(".log.1")
            try:
                if velho.exists():
                    velho.unlink()
                _LOG_PASSOS.rename(velho)
            except Exception:
                pass
        with open(_LOG_PASSOS, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + "  " + str(texto).replace("\n", " / ")[:1500] + "\n")
    except Exception:
        pass


# UMA TAREFA DE TELA POR VEZ (23/09/2026). O log da VPS mostrou, as 01:04 de
# 23/09, DUAS tarefas do mesmo PC se alternando passo a passo por tres
# minutos ("desabilite as notificacoes..." e "desabilitar todas as
# notificacoes...") — uma vinda da regra local da fala, outra do modelo
# chamando a ferramenta. Cada passo de uma encerrava a sessao da outra na VPS,
# e as duas mexiam no mesmo mouse. Agora a tarefa nova PEDE para a anterior
# parar, espera ela soltar, e so entao comeca.
_TRAVA_DO_LACO = threading.Lock()
_LACO_ATUAL: dict = {"parar": None}

# Um movimento nestes so acontece depois do "sim" do dono.
_PERIGOSO = re.compile(
    # As formas de MANDAR entram junto com o infinitivo (04/09/2026): o dono
    # fala "apagUE", "excluA", "remova", nao "apagar". Sem elas, o pedido dele
    # nao era reconhecido como pedido de risco — e a autorizacao que ja vinha
    # na propria frase se perdia, fazendo o JARVIS perguntar o que ele acabara de
    # mandar fazer.
    r"\b(pagar|pague|paga|pagamento|comprar|compre|compra|finalizar (a )?compra|checkout|"
    r"excluir|exclui|exclua|apagar|apaga|apague|deletar|delete|deleta|"
    r"remover|remove|remova|formatar|formate|"
    r"enviar|envie|envia|send|transferir|transfira|transfer|desinstalar|desinstale|uninstall|"
    r"confirmar (o )?pedido|assinar|assine|contratar|cancelar (a )?conta|"
    r"redefinir|resetar|resete|shutdown|desligar|desligue|reiniciar|reinicie)\b", re.I)

_SIM = re.compile(r"\b(sim|pode|confirmo|vai|manda|ok|isso|claro|autorizo|libera)\b", re.I)


class FreioEsc:
    """ESC apertado em QUALQUER lugar para o computer use na hora.

    Por que existe (04/09/2026, pedido do Diego depois do primeiro teste ao
    vivo): parar por VOZ funciona, mas custa o tempo de falar, o tempo de o
    ouvido transcrever e o tempo de a frase casar com o freio — dois ou tres
    segundos em que o mouse continua andando. O Codex, no terminal, para com
    ESC; e o gesto que a pessoa ja tem no dedo quando se assusta.

    Como: uma thread pergunta ao Windows, 20 vezes por segundo, se a tecla ESC
    esta pressionada AGORA (`GetAsyncKeyState`, que enxerga a tecla em qualquer
    janela — o teclado nao e "de" um programa). Custa praticamente nada e nao
    depende de gancho de teclado, que exigiria laco de mensagens numa thread de
    trabalho (o mesmo problema que o UI Automation tem em STA).

    A ARMADILHA: o proprio JARVIS aperta ESC durante as tarefas (fechar menu,
    cancelar caixa de dialogo). O `GetAsyncKeyState` nao distingue tecla de
    gente de tecla de programa, entao ele se pararia sozinho. Por isso o laco
    avisa `ignorar_por()` antes de cada ESC que ele mesmo manda.
    """

    _VK_ESCAPE = 0x1B

    def __init__(self, ao_apertar=None):
        self._apertado = False
        self._fim = threading.Event()
        self._ignorar_ate = 0.0
        self._thread = None
        # AVISO NA HORA (08/10/2026). Queixa do Diego: "quando eu aperto ESC
        # para cancelar o computer use ele demora demais para finalizar e
        # avisar que parou". O laco so via a tecla entre um passo e outro, e o
        # "parei" saia pela voz de rede DEPOIS de tudo. `ao_apertar` e chamado
        # desta thread no instante da tecla: quem chama corta a fala que esta
        # tocando e ja diz que parou, sem esperar o laco terminar.
        self._ao_apertar = ao_apertar

    def _marcar(self) -> None:
        if self._apertado:
            return
        self._apertado = True
        cb = self._ao_apertar
        if cb is not None:
            try:
                cb()
            except Exception:
                pass

    def ligar(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._laco, daemon=True, name="freio-esc")
        self._thread.start()

    def parar(self) -> None:
        self._fim.set()
        self._thread = None

    def ignorar_por(self, segundos: float = 0.8) -> None:
        """O ESC que sair nos proximos `segundos` e do proprio JARVIS, nao do dono."""
        self._ignorar_ate = time.monotonic() + float(segundos)

    @property
    def apertado(self) -> bool:
        return self._apertado

    def _laco_evdev(self) -> bool:
        """ESC lido direto do teclado (/dev/input) — vale no Wayland e no X11.

        26/09/2026: no Wayland o XQueryKeymap so enxerga janela do XWayland.
        O dono do Big Linux esta no grupo `input`, entao o JARVIS le os eventos
        do teclado de verdade. Devolve False se nao houver teclado legivel
        (ai quem chama tenta pelo X11)."""
        import select  # noqa: PLC0415
        import struct  # noqa: PLC0415
        import re as _re  # noqa: PLC0415
        try:
            blocos = open("/proc/bus/input/devices", encoding="utf-8", errors="ignore").read().split("\n\n")
        except Exception:
            return False
        fds = []
        for b in blocos:
            nome = _re.search(r'N: Name="([^"]*)"', b)
            hand = _re.search(r"H: Handlers=([^\n]*)", b)
            chaves = _re.search(r"B: KEY=([0-9a-f ]+)", b)
            if not (nome and hand and chaves) or "JARVIS" in nome.group(1):
                continue
            ev = _re.search(r"event\d+", hand.group(1))
            if "kbd" not in hand.group(1) or not ev:
                continue
            # o bit do KEY_ESC (1) na ultima palavra do mapa de teclas
            if not (int(chaves.group(1).split()[-1], 16) & (1 << 1)):
                continue
            try:
                fds.append(os.open(f"/dev/input/{ev.group(0)}", os.O_RDONLY | os.O_NONBLOCK))
            except OSError:
                continue
        if not fds:
            return False
        formato = struct.Struct("llHHi")
        try:
            while not self._fim.is_set():
                prontos, _, _ = select.select(fds, [], [], 0.1)
                for fd in prontos:
                    try:
                        dados = os.read(fd, formato.size * 64)
                    except OSError:
                        continue
                    for i in range(0, len(dados) - formato.size + 1, formato.size):
                        _s, _us, tipo, codigo, valor = formato.unpack_from(dados, i)
                        if (tipo == 1 and codigo == 1 and valor == 1
                                and time.monotonic() >= self._ignorar_ate):
                            self._marcar()
                            return True
        finally:
            for fd in fds:
                try:
                    os.close(fd)
                except OSError:
                    pass
        return True

    def _laco_linux(self) -> None:
        """LINUX (26/09/2026): o mesmo freio pelo X11.

        O `ctypes.windll` nao existe no Linux e o laco de baixo morria no
        primeiro `except` — calado: o cliente do Big Linux apertava ESC e o
        JARVIS seguia mexendo. `XQueryKeymap` devolve o teclado INTEIRO agora,
        em qualquer janela, que e o que o `GetAsyncKeyState` faz no Windows."""
        try:
            import ctypes  # noqa: PLC0415
            import ctypes.util  # noqa: PLC0415
            x11 = ctypes.cdll.LoadLibrary(ctypes.util.find_library("X11") or "libX11.so.6")
            x11.XOpenDisplay.restype = ctypes.c_void_p
            x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
            x11.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
            x11.XKeysymToKeycode.restype = ctypes.c_ubyte
            x11.XQueryKeymap.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
            tela = x11.XOpenDisplay(None)
            if not tela:
                return
            tecla = x11.XKeysymToKeycode(tela, 0xFF1B)      # XK_Escape
            mapa = ctypes.create_string_buffer(32)
        except Exception:
            return
        while not self._fim.is_set():
            try:
                if time.monotonic() >= self._ignorar_ate:
                    x11.XQueryKeymap(tela, mapa)
                    if mapa.raw[tecla // 8] & (1 << (tecla % 8)):
                        self._marcar()
                        return
            except Exception:
                return
            time.sleep(0.05)

    def _laco(self) -> None:
        import sys  # noqa: PLC0415
        if sys.platform.startswith("linux"):
            if self._laco_evdev():
                return None
            return self._laco_linux()
        try:
            import ctypes  # noqa: PLC0415
            user32 = ctypes.windll.user32
            user32.GetAsyncKeyState.restype = ctypes.c_short
            # Primeira leitura jogada fora: ela vem com o que ficou acumulado
            # desde a ultima consulta de quem quer que seja.
            user32.GetAsyncKeyState(self._VK_ESCAPE)
        except Exception:
            return
        while not self._fim.is_set():
            try:
                if time.monotonic() >= self._ignorar_ate:
                    if user32.GetAsyncKeyState(self._VK_ESCAPE) & 0x8000:
                        self._marcar()
                        return
            except Exception:
                return
            time.sleep(0.05)


# A RESPOSTA DELE PODE SER UMA PERGUNTA (04/09/2026, pedido do Diego).
#
# Aconteceu no teste: o JARVIS perguntou "posso apagar?" e o dono respondeu
# querendo saber se apagar aquilo estragava alguma coisa no computador. Isso
# NAO e um "nao" — e uma duvida, e quem tem de responder e ele. Antes, qualquer
# resposta que nao tivesse "sim" cancelava o passo e a tarefa morria ali.
#
# Agora a duvida vai para o Codex (que esta com a tela na frente e conhece a
# tarefa), a resposta e falada, e a pergunta de confirmacao volta.
_DUVIDA = re.compile(
    r"\?"
    r"|^\s*(o que|oq|qual|quais|quanto|quantos|quando|onde|como|por que|porque|"
    r"pra que|para que|sera|sera que|isso vai|isso nao|esses|essas|isso e)\b"
    r"|\b(afeta|afetar|estraga|estragar|quebra|quebrar|prejudica|atrapalha|"
    r"e seguro|eh seguro|tem certeza|tem risco|perigoso|precisa mesmo|"
    r"o que sao|o que e|pra que serve|para que serve|posso perder|"
    r"perde alguma coisa|da problema|tem problema|vai dar problema)\b", re.I)


def _parece_duvida(texto: str) -> bool:
    """A resposta dele foi uma pergunta, e nao um sim ou um nao?"""
    t = _sem_acento_simples(str(texto or ""))
    return bool(t) and bool(_DUVIDA.search(t))


# O Codex pedindo LICENCA para seguir ("posso prosseguir?", "posso excluir?").
# Diferente de uma pergunta de dado ("qual e a placa do carro?"), que so o dono
# sabe responder: licenca ja dada nao se pede de novo.
_PEDE_PERMISSAO = re.compile(
    r"\b(posso|podemos|autoriza|autorizo|confirma|confirmo|permite|permito|"
    r"pode (eu |seguir|continuar|prosseguir|excluir|apagar|deletar)|"
    r"prosseguir|seguir em frente|dou (sequencia|continuidade)|"
    r"deseja que eu|quer que eu)\b", re.I)

# A fala de quem acha que esta diante da tela de permissao do Windows.
_FALA_DE_PERMISSAO = re.compile(
    r"\b(uac|permissao de administrador|permissoes de administrador|"
    r"tela de permissao|janela de permissao|controle de conta|"
    r"clique em continuar|clicar em continuar|botao continuar|"
    r"privilegios de administrador|como administrador)\b", re.I)


def _pede_permissao(texto: str) -> bool:
    t = _sem_acento_simples(str(texto or ""))
    if not _PEDE_PERMISSAO.search(t):
        return False
    # "QUAL pasta voce quer que eu abra?" nao e licenca — e pergunta de DADO, e
    # so o dono sabe responder. Sem esta ressalva, o "quer que eu" fazia o laco
    # responder sozinho "ja autorizado" a uma pergunta que ele precisava ouvir.
    if (re.search(r"\b(qual|quais|onde|quem|quanto|quantos|o que|que arquivo|que pasta|"
                  r"que nome|que valor)\b", t)
            and not re.search(r"\bposso\b|\bautoriz|\bconfirm|\bprossegu", t)):
        return False
    return True


def _fala_de_permissao(texto: str) -> bool:
    return bool(_FALA_DE_PERMISSAO.search(_sem_acento_simples(str(texto or ""))))


# ── DE QUE TIPO E O RISCO ──────────────────────────────────────────────────
#
# Pedir autorizacao e certo; pedir a MESMA autorizacao tres vezes na mesma
# tarefa e o que fez o Diego desistir no meio (04/09/2026): ele mandou apagar os
# arquivos temporarios, o JARVIS perguntou "posso?", ele respondeu que sim, o JARVIS
# perguntou DE NOVO no passo seguinte (a caixa do Windows pedindo confirmacao da
# exclusao — o mesmo apagar, so que na tela), e como ele nao respondeu a segunda,
# a tarefa morreu com "Certo, nao fiz".
#
# Agora a autorizacao vale por FAMILIA e pela tarefa inteira: autorizou apagar,
# nao pergunta de novo para apagar. Mas continua perguntando para uma familia
# DIFERENTE — quem liberou apagar temporarios nao liberou comprar nem formatar.
_FAMILIAS = (
    # AS FORMAS DE MANDAR TAMBEM CONTAM. O dono nao fala no infinitivo: ele diz
    # "apagUE esses arquivos", "excluA isso", "remova aquilo". Sem essas formas,
    # a familia saia "outro" e a autorizacao que vem no proprio pedido nunca
    # valia — que e justamente o caso que ele reclamou (04/09/2026).
    ("apagar", r"excluir|exclui|exclua|apagar|apaga|apague|apagando|deletar|delete|"
               r"deleta|remover|remove|remova|lixeira|limpar|limpa|limpe"),
    ("pagar", r"pagar|pague|paga|pagamento|comprar|compre|compra|checkout|"
              r"finalizar (a )?compra|confirmar (o )?pedido|assinar|assine|contratar"),
    ("enviar", r"enviar|envie|envia|send|transferir|transfira|transfer|mandar|mande"),
    ("formatar", r"formatar|formate|format"),
    ("desinstalar", r"desinstalar|desinstale|uninstall"),
    ("desligar", r"desligar|desligue|shutdown|reiniciar|reinicie|redefinir|resetar|resete"),
    ("cancelar_conta", r"cancelar (a )?conta|cancele (a )?conta"),
)


def _familia_do_risco(descricao: str, acao: dict) -> str:
    """Que TIPO de coisa sem volta este movimento faz ("apagar", "pagar"...)."""
    # Apagar SEM LIXEIRA e familia propria, e nunca vem autorizada de fabrica:
    # "apaga os temporarios" autoriza mandar para a lixeira, de onde da para
    # voltar. Shift+Delete nao tem volta nenhuma, e isso o dono tem de ouvir.
    if _tecla_sem_volta(acao):
        return "apagar_de_vez"
    texto = _sem_acento_simples(f"{descricao} {acao.get('acao') or ''} {acao.get('teclas') or ''}")
    for nome, padrao in _FAMILIAS:
        if re.search(padrao, texto, re.I):
            return nome
    return "outro"


def _tecla_sem_volta(acao: dict) -> bool:
    """Atalho de teclado que apaga SEM ir para a lixeira: pergunta antes.

    O portao do `_PERIGOSO` le o TEXTO do movimento, e um atalho nao tem texto:
    `{"acao":"tecla","teclas":"shift+delete"}` passava direto, apagando de vez —
    e nem o "pode desfazer" alcanca isso depois. Delete sozinho continua sem
    perguntar de proposito: ele manda para a lixeira, e o dono pode voltar
    atras. A diferenca entre os dois e exatamente esta.
    """
    if str(acao.get("acao") or "").lower().strip() not in ("tecla", "key", "atalho", "hotkey"):
        return False
    teclas = _sem_acento_simples(str(acao.get("teclas") or acao.get("tecla") or ""))
    return "shift" in teclas and ("del" in teclas or "excluir" in teclas)


# ── executar UM movimento ──────────────────────────────────────────────────

def _sem_acento_simples(texto: str) -> str:
    import unicodedata  # noqa: PLC0415
    return "".join(c for c in unicodedata.normalize("NFD", (texto or "").lower())
                   if unicodedata.category(c) != "Mn").strip()


# TODO O TECLADO, e nao so o que eu lembrei de por (03/09/2026).
#
# O dono pediu "todos os atalhos". Atalho no Windows e sempre a mesma coisa:
# teclas com "+" no meio. Entao o que importa aqui nao e listar atalhos (sao
# milhares) — e garantir que TODA TECLA tenha nome, inclusive os nomes que a
# pessoa e o modelo usam em portugues. Com isto, qualquer atalho existente
# funciona: win+r, ctrl+shift+esc, alt+f4, ctrl+alt+del, f2, win+ponto...
_TECLAS = {
    # nomes em portugues
    "controle": "ctrl", "control": "ctrl", "alternar": "alt", "opcao": "alt",
    "cmd": "win", "windows": "win", "super": "win", "janela": "win",
    "maiuscula": "shift", "maiusculo": "shift", "shift esquerdo": "shift",
    "esc": "esc", "escape": "esc", "sair": "esc",
    "voltar": "backspace", "apagar": "backspace", "retrocesso": "backspace",
    "espaco": "space", "espaço": "space", "barra de espaco": "space",
    "tabulacao": "tab", "tabulação": "tab",
    "entra": "enter", "return": "enter", "confirma": "enter",
    "del": "delete", "deletar": "delete", "excluir": "delete",
    "inserir": "insert", "ins": "insert",
    "inicio": "home", "início": "home", "fim": "end",
    "pagedown": "pgdn", "pageup": "pgup", "page down": "pgdn", "page up": "pgup",
    "pagina abaixo": "pgdn", "pagina acima": "pgup",
    "cima": "up", "baixo": "down", "esquerda": "left", "direita": "right",
    "seta cima": "up", "seta baixo": "down", "seta esquerda": "left",
    "seta direita": "right", "para cima": "up", "para baixo": "down",
    "imprimir tela": "printscreen", "print": "printscreen", "prtsc": "printscreen",
    "menu de contexto": "apps", "botao direito": "apps", "contexto": "apps",
    "capslock": "capslock", "numlock": "numlock", "scrolllock": "scrolllock",
    "pausa": "pause", "ponto": ".", "virgula": ",", "menos": "-", "mais": "+",
    # UMA barra invertida, nao duas: no dicionario do Bob estava "\\\\", que em
    # Python e o texto de DUAS barras — e o pyautogui, que compara o nome com a
    # lista dele, nao conhece "\\" e nao apertava tecla nenhuma.
    "barra": "/", "contrabarra": "\\", "igual": "=",
    # midia e volume (o pyautogui conhece estes nomes)
    "volume mais": "volumeup", "volume menos": "volumedown", "mudo": "volumemute",
    "tocar": "playpause", "pausar": "playpause", "proxima faixa": "nexttrack",
    "faixa anterior": "prevtrack",
}
# F1..F24 e o teclado numerico entram sozinhos, sem lista escrita a mao.
for _i in range(1, 25):
    _TECLAS[f"f{_i}"] = f"f{_i}"
for _i in range(10):
    _TECLAS[f"num {_i}"] = f"num{_i}"


def _mapa_teclas(texto: str) -> list[str]:
    """'ctrl+l' -> ['ctrl','l']; 'entra' -> ['enter']. Aceita nome em portugues."""
    partes = [p.strip().lower() for p in re.split(r"\s*\+\s*", str(texto or "")) if p.strip()]
    saida = []
    for p in partes:
        saida.append(_TECLAS.get(p, _TECLAS.get(_sem_acento_simples(p), p)))
    return saida


def _teclas_conhecidas(teclas: list[str]) -> list[str]:
    """As que o pyautogui NAO conhece — porque ele ignora nome errado em silencio.

    `pyautogui.press("controle")` nao levanta erro nenhum: nao aperta nada e
    volta como se tivesse apertado. O historico ficava dizendo "apertei
    controle+a" numa volta em que nada aconteceu, e o Codex seguia adiante
    achando que o texto ja estava selecionado. Melhor declarar a falha.
    """
    try:
        import pyautogui  # noqa: PLC0415
        validas = set(pyautogui.KEYBOARD_KEYS)
    except Exception:
        return []
    return [t for t in teclas if t not in validas]


def _num(valor, padrao: float = 0.0) -> float:
    """Numero que o modelo mandou, sem derrubar o passo.

    Ele manda "5", 5, 5.0 e as vezes "5 vezes". Um `int("5.0")` levanta
    ValueError e o movimento inteiro virava "deu erro" — por causa da grafia.
    """
    try:
        return float(valor)
    except (TypeError, ValueError):
        try:
            achado = re.search(r"-?\d+(?:[.,]\d+)?", str(valor or ""))
            return float(achado.group(0).replace(",", ".")) if achado else float(padrao)
        except Exception:
            return float(padrao)


def _inteiro(valor, padrao: int, minimo: int, maximo: int) -> int:
    return max(minimo, min(maximo, int(round(_num(valor, padrao)))))


def _alvo(acao: dict, ctx: dict, ponta: str = "") -> tuple[int | None, int | None, str]:
    """Onde este movimento cai, em pixel REAL da tela.

    Quatro jeitos, nesta ordem:
      1. "elemento": 12   -> centro exato do elemento numerado (o preferido);
      2. "nome": "Placa"  -> procura pelo nome AGORA (e o que as receitas usam);
      3. "x","y"          -> pixel da FOTO, convertido para a tela;
      4. nada             -> onde o mouse ja esta.

    `ponta="2"` le os campos da SEGUNDA ponta (`elemento2`, `nome2`, `x2`,
    `y2`) — e o destino do arrastar. Antes so a origem podia ser um elemento e
    o destino era obrigatoriamente pixel: para "arraste a pasta A para dentro da
    pasta B" isso obrigava o Codex a chutar onde B estava, que e exatamente o
    chute que a lista de elementos existe para evitar.
    """
    p = str(ponta or "")
    n = acao.get(f"elemento{p}")
    if n is None and not p:
        n = acao.get("n")
    if n is not None:
        try:
            el = ctx["por_numero"].get(int(n))
        except (TypeError, ValueError):
            el = None
        if el:
            return int(el["x"]), int(el["y"]), f'{el["tipo"]} "{el["nome"]}"'[:60]
        return None, None, f"elemento {n} nao existe nesta tela"
    nome = str(acao.get(f"nome{p}") or "").strip()
    if nome and acao.get(f"x{p}") is None:
        el = tela_visao.achar_por_nome(nome)
        if el:
            return int(el["x"]), int(el["y"]), f'{el["tipo"]} "{el["nome"]}"'[:60]
        return None, None, f"nao achei {nome!r} na tela"
    x, y = acao.get(f"x{p}"), acao.get(f"y{p}")
    if x is None or y is None:
        return None, None, ""
    try:
        # `desloc_*`: onde a foto comeca na tela. E zero, menos no passo em que
        # a foto veio AMPLIADA (acao `ampliar`) — ai x,y sao da foto ampliada.
        return (int(round(_num(x) * ctx["escala_x"] + ctx.get("desloc_x", 0))),
                int(round(_num(y) * ctx["escala_y"] + ctx.get("desloc_y", 0))), f"ponto ({x},{y})")
    except (TypeError, ValueError):
        return None, None, ""


# Quem mira em algum lugar da tela. O resto (expandir, acionar, rolar_ate,
# clicar_nome, abrir_url...) procura sozinho pelo nome e nao paga a varredura.
_COM_ALVO = {
    "clicar", "click", "clicar_duplo", "double_click", "clicar_direito",
    "right_click", "menu_direito", "mover", "move", "hover",
    "arrastar", "drag", "arrastar_para", "soltar_em",
    "digitar", "type", "escrever", "credencial", "senha", "login",
    "rolar", "scroll",
}

_MODIFICADORES_VALIDOS = ("ctrl", "shift", "alt", "win")


def _modificadores(acao: dict) -> list[str]:
    """As teclas que ficam SEGURADAS durante o clique (ctrl, shift, alt)."""
    bruto = (acao.get("modificador") or acao.get("modificadores")
             or acao.get("tecla_junto") or acao.get("com_tecla") or "")
    if isinstance(bruto, (list, tuple)):
        bruto = "+".join(str(b) for b in bruto)
    return [t for t in _mapa_teclas(str(bruto)) if t in _MODIFICADORES_VALIDOS]


def _arrastar(x1: int, y1: int, x2: int, y2: int) -> None:
    """Arrasta de um ponto ao outro do jeito que o Windows entende.

    `pyautogui.dragTo` num salto so NAO funciona no Explorador de Arquivos nem
    em pagina de navegador: os dois so comecam a arrastar depois de ver o mouse
    ANDAR alguns pixels com o botao apertado (o Windows exige uns 4 px; o
    navegador exige uma sequencia de eventos de movimento). Num salto unico o
    Explorer entende clique, e o arquivo fica exatamente onde estava — o
    sintoma e "ele disse que arrastou e nao arrastou".

    Por isso: aperta, espera, anda em 12 pedacos, PARA em cima do destino por
    um instante (a pasta de destino precisa acender antes de soltar) e so
    entao solta.
    """
    import pyautogui  # noqa: PLC0415
    pyautogui.moveTo(int(x1), int(y1), duration=0.15)
    pyautogui.mouseDown()
    time.sleep(0.18)
    passos = 12
    for i in range(1, passos + 1):
        pyautogui.moveTo(int(x1 + (x2 - x1) * i / passos),
                         int(y1 + (y2 - y1) * i / passos), duration=0.03)
    time.sleep(0.4)
    pyautogui.moveTo(int(x2), int(y2))
    pyautogui.mouseUp()
    time.sleep(0.45)


def _pediu_alvo(acao: dict, ponta: str = "") -> bool:
    """O movimento APONTOU para algum lugar (elemento, nome ou x,y)?

    Serve para separar "digita onde estiver o foco" (legitimo) de "digita no
    elemento 12" quando o 12 nao existe mais — que e erro e nao pode virar
    texto digitado as cegas na janela errada.
    """
    p = str(ponta or "")
    return any(acao.get(k) is not None and str(acao.get(k)).strip() != ""
               for k in (f"elemento{p}", f"nome{p}", f"x{p}"))


def _digitar(texto: str) -> None:
    """Escreve o texto na janela em foco, sem estragar a area de transferencia.

    Colar e mais fiel que teclar (acento e simbolo fora do layout somem no
    pyautogui), mas roubar a area de transferencia do dono e falta de educacao:
    o que estava la volta no fim.
    """
    import pyautogui  # noqa: PLC0415
    guardado = None
    try:
        import pyperclip  # noqa: PLC0415
        try:
            guardado = pyperclip.paste()
        except Exception:
            guardado = None
        pyperclip.copy(texto)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.12)
    except Exception:
        try:
            from actions.digitar_na_tela import digitar_texto  # noqa: PLC0415
            digitar_texto(texto, intervalo=0.008, pausa_linha=0.06)
        except Exception:
            pyautogui.typewrite(texto, interval=0.02)
    finally:
        if guardado is not None:
            try:
                import pyperclip  # noqa: PLC0415
                pyperclip.copy(guardado)
            except Exception:
                pass


def _dormir(segundos: float, ctx: dict | None = None) -> None:
    """Espera que ACORDA quando o dono manda parar.

    Um `time.sleep(5)` no meio de um lote deixava o freio de mao preso por
    cinco segundos: o dono falava "para", o Bob dizia que ia parar, e o mouse
    continuava andando. Agora o sono e picado em fatias de 0,15 s.
    """
    parar = (ctx or {}).get("deve_parar")
    fim = time.monotonic() + max(0.0, float(segundos))
    while time.monotonic() < fim:
        if parar and parar():
            return
        time.sleep(min(0.15, max(0.01, fim - time.monotonic())))


def _uac_aberto() -> bool:
    """O Windows esta pedindo permissao de administrador (a tela azul do UAC)?

    Instalar e desinstalar programa quase sempre passa por aqui. Essa tela roda
    numa "area de trabalho segura": NENHUM programa consegue clicar nela — e
    isso e proposital, e a defesa do Windows contra exatamente o que o Bob faz.
    Entao ele nao tenta: reconhece, pede ao dono que confirme e espera.
    """
    try:
        import psutil  # noqa: PLC0415
        for pr in psutil.process_iter(["name"]):
            if (pr.info.get("name") or "").lower() == "consent.exe":
                return True
    except Exception:
        pass
    return False


_PASTAS_CONHECIDAS = {
    "area de trabalho": "Desktop", "desktop": "Desktop", "tela": "Desktop",
    "documentos": "Documents", "meus documentos": "Documents", "documents": "Documents",
    "downloads": "Downloads", "download": "Downloads", "transferencias": "Downloads",
    "imagens": "Pictures", "fotos": "Pictures", "pictures": "Pictures",
    "musicas": "Music", "music": "Music",
    "videos": "Videos", "video": "Videos",
}


def _resolver_pasta(caminho: str):
    """O caminho de verdade da pasta que a pessoa (ou o Codex) chamou pelo nome.

    Resolve tres coisas que aparecem o tempo todo:
      - o nome em portugues ("area de trabalho", "downloads");
      - a SUBPASTA escrita depois dele ("documentos\\clientes\\AJ");
      - o caminho completo, com variavel de ambiente ou ~ no meio.

    Se nada disso existir, procura uma pasta com esse nome ate dois niveis
    abaixo dos lugares onde o dono guarda coisa (a casa dele, Desktop,
    Documentos, Downloads) — e devolve None quando nao acha, porque abrir a
    pasta errada e pior do que dizer que nao achou.
    """
    from pathlib import Path  # noqa: PLC0415
    import os  # noqa: PLC0415
    # A BARRA DEPENDE DO SISTEMA (05/09/2026, porte para Mac). No Windows a
    # barra invertida e a separadora e trocar "/" por "\" ajuda; no Mac isso
    # DESTROI o caminho — "/Users/fulano/Downloads" virava um nome so.
    import platform as _plat  # noqa: PLC0415
    _win = _plat.system() == "Windows"
    _sep = "\\" if _win else "/"
    bruto = str(caminho or "").strip().strip('"')
    bruto = bruto.replace("/", "\\") if _win else bruto.replace("\\", "/")
    if not bruto:
        return None
    direto = Path(os.path.expandvars(bruto)).expanduser()
    if direto.is_dir():
        return str(direto)
    casa = Path.home()
    partes = [p for p in bruto.split(_sep) if p.strip()]
    if partes:
        raiz = _PASTAS_CONHECIDAS.get(_sem_acento_simples(partes[0]))
        if raiz:
            tentativa = casa / raiz
            for pedaco in partes[1:]:
                tentativa = tentativa / pedaco
            if tentativa.is_dir():
                return str(tentativa)
    alvo = _sem_acento_simples(partes[-1]) if partes else ""
    if not alvo:
        return None
    bases = [casa] + [casa / n for n in ("Desktop", "Documents", "Downloads")]
    for base in bases:
        try:
            if not base.is_dir():
                continue
            for filho in base.iterdir():
                if not filho.is_dir():
                    continue
                if _sem_acento_simples(filho.name) == alvo:
                    return str(filho)
                try:
                    for neto in filho.iterdir():
                        if neto.is_dir() and _sem_acento_simples(neto.name) == alvo:
                            return str(neto)
                except (OSError, PermissionError):
                    continue
        except (OSError, PermissionError):
            continue
    return None


def _trazer_para_frente(hwnd: int) -> bool:
    """Poe a janela na frente de verdade, mesmo com video em tela cheia.

    Por que (23/09/2026, teste real): com o YouTube em tela cheia na frente, o
    Windows abria a Calculadora ATRAS dele e o `SetForegroundWindow` simples
    era recusado (o Windows nao deixa programa de fundo roubar a frente). O
    JARVIS ficou quatro passos tentando alt+tab e abrindo a calculadora de
    novo. Aqui vao, em ordem, os jeitos que o proprio Windows aceita — sem o
    truque do Alt+Esc, que tira o video da tela cheia e dispara o freio do ESC.
    """
    try:
        import win32con  # noqa: PLC0415
        import win32gui  # noqa: PLC0415
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        if not hwnd or not win32gui.IsWindow(hwnd):
            return False
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            time.sleep(0.15)
        if user32.GetForegroundWindow() == hwnd:
            return True
        try:
            user32.SetForegroundWindow(hwnd)
        except Exception:
            pass
        time.sleep(0.12)
        if user32.GetForegroundWindow() == hwnd:
            return True
        # Ligar a fila de entrada desta thread a da janela da frente: com isso
        # o Windows trata o pedido como vindo "de quem esta na frente".
        frente = user32.GetForegroundWindow()
        tid_frente = user32.GetWindowThreadProcessId(frente, None)
        tid_meu = kernel32.GetCurrentThreadId()
        ligou = False
        try:
            if tid_frente and tid_frente != tid_meu:
                ligou = bool(user32.AttachThreadInput(tid_meu, tid_frente, True))
            user32.BringWindowToTop(hwnd)
            # Acima de tudo por um instante (inclusive da tela cheia) e de volta
            # ao normal: a janela fica na frente sem ficar "sempre visivel".
            flags = 0x0001 | 0x0002 | 0x0040          # NOSIZE | NOMOVE | SHOWWINDOW
            user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, flags)   # HWND_TOPMOST
            user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, flags)   # HWND_NOTOPMOST
            user32.SetForegroundWindow(hwnd)
            user32.SetActiveWindow(hwnd)
        finally:
            if ligou:
                user32.AttachThreadInput(tid_meu, tid_frente, False)
        time.sleep(0.15)
        if user32.GetForegroundWindow() == hwnd:
            return True
        try:
            user32.SwitchToThisWindow(hwnd, True)
        except Exception:
            pass
        time.sleep(0.2)
        return user32.GetForegroundWindow() == hwnd
    except Exception:
        return False


def _janelas_visiveis() -> dict:
    """{hwnd: titulo} das janelas de verdade (visiveis, com titulo, nao minusculas)."""
    saida: dict = {}
    try:
        import win32gui  # noqa: PLC0415

        def _cb(h, _):
            try:
                if not win32gui.IsWindowVisible(h):
                    return True
                t = (win32gui.GetWindowText(h) or "").strip()
                if not t or t in ("Program Manager",):
                    return True
                e, d, dx, dy = win32gui.GetWindowRect(h)
                if (dx - e) < 150 or (dy - d) < 100:
                    return True
                saida[h] = t
            except Exception:
                pass
            return True
        win32gui.EnumWindows(_cb, None)
    except Exception:
        pass
    return saida


def _frente_para_o_que_abriu(antes: dict, nome: str, limite_s: float = 4.0) -> str:
    """Depois de abrir programa/pasta: acha a janela que nasceu e poe na frente.

    Programa aberto por processo de fundo nasce ATRAS da janela da frente
    (o teste da Calculadora atras do YouTube). Primeiro procura janela NOVA; se
    o programa ja estava aberto (nada novo), procura pelo nome.
    """
    from actions.digitar_na_tela import _achar_janela  # noqa: PLC0415
    fim = time.monotonic() + limite_s
    alvo = 0
    while time.monotonic() < fim and not alvo:
        novas = [h for h in _janelas_visiveis() if h not in antes]
        if novas:
            alvo = novas[0]
            break
        time.sleep(0.25)
    if not alvo and nome:
        termos = [p for p in _sem_acento_simples(nome.lower()).split() if len(p) >= 4] or [nome.lower()]
        try:
            alvo = _achar_janela(termos[:1]) or _achar_janela([nome.lower()])
        except Exception:
            alvo = 0
    if not alvo:
        return ""
    try:
        import win32gui  # noqa: PLC0415
        titulo = win32gui.GetWindowText(alvo) or ""
    except Exception:
        titulo = ""
    if _trazer_para_frente(alvo):
        return f" (a janela '{titulo[:50]}' esta na frente)"
    return f" (a janela '{titulo[:50]}' abriu, mas o Windows nao deixou por na frente)"


# ── INTERRUPTOR JA NO ESTADO PEDIDO (23/09/2026, queixa do Diego) ─────────
#
# "Eu peco para desativar as notificacoes do Windows Defender, ele ativou as
#  notificacoes do sistema, depois desativou as do Defender, e ativou, e ficou
#  assim o tempo todo ativando e desativando."
#
# A trava de 22/09 (`_desfaria`) so segura o que ELE JA mexeu nesta tarefa. O
# que faltava e a regra mais simples de todas: se o pedido e DESLIGAR, clicar
# num interruptor que JA ESTA desligado so pode ligar — e vice-versa. O estado
# vem do proprio Windows (`tela_visao._estado_do_elemento`), nao da foto.
# Pedido com os dois verbos ("desligue X e ligue Y") nao tem direcao unica, e
# ai esta trava fica de fora.
_QUER_DESLIGAR = re.compile(r"\b(desativ|deslig|desabilit|desmarc|silenci)\w*", re.I)
_QUER_LIGAR = re.compile(r"\b(ativ|lig|habilit|marqu|marca)\w*", re.I)


def _intencao_da_tarefa(tarefa: str) -> str:
    t = _sem_acento_simples(str(tarefa or "").lower())
    desl, lig = bool(_QUER_DESLIGAR.search(t)), bool(_QUER_LIGAR.search(t))
    if desl and not lig:
        return "desligar"
    if lig and not desl:
        return "ligar"
    return ""


def _clique_contra_o_pedido(acao: dict, ctx: dict, intencao: str) -> str:
    """Recado de bloqueio se o clique vai CONTRA o pedido; "" se pode clicar."""
    if not intencao:
        return ""
    tipo = str(acao.get("acao") or "").lower().strip()
    if tipo not in ("clicar", "click", "clicar_nome", "clicar_texto", "acionar", "apertar"):
        return ""
    el = None
    n = acao.get("elemento")
    if n is not None:
        try:
            el = (ctx.get("por_numero") or {}).get(int(n))
        except (TypeError, ValueError):
            el = None
    elif acao.get("nome"):
        alvo = _sem_acento_simples(str(acao.get("nome")).lower().strip())
        for cand in (ctx.get("por_numero") or {}).values():
            if _sem_acento_simples((cand.get("nome") or "").lower().strip()) == alvo:
                el = cand
                break
    if not el:
        return ""
    estado = set(el.get("estado") or [])
    nome = (el.get("nome") or "")[:70]
    if intencao == "desligar" and estado & {"desligado", "desmarcado"}:
        return (f"NAO CLIQUEI em '{nome}': ele JA ESTA {'desligado' if 'desligado' in estado else 'desmarcado'}, "
                f"e o pedido e DESLIGAR — clicar agora LIGARIA. Deixe como esta e procure o que ainda "
                f"esta LIGADO/MARCADO na lista de elementos. Se tudo o que o pedido cobre ja esta "
                f"desligado, responda concluido.")
    if intencao == "ligar" and estado & {"ligado", "marcado"}:
        return (f"NAO CLIQUEI em '{nome}': ele JA ESTA {'ligado' if 'ligado' in estado else 'marcado'}, "
                f"e o pedido e LIGAR — clicar agora DESLIGARIA. Deixe como esta e procure o que ainda "
                f"esta DESLIGADO/DESMARCADO. Se tudo o que o pedido cobre ja esta ligado, responda "
                f"concluido.")
    return ""


# Onde ficam as notificacoes do Defender — o caminho que ele errava.
_FALA_DO_DEFENDER = re.compile(r"(defender|seguranca do windows|antivirus|anti-virus|firewall)", re.I)
_RECEITA_NOTIFICACOES_DEFENDER = (
    "WINDOWS DEFENDER = o app SEGURANCA DO WINDOWS. Onde fica cada coisa:\n"
    "  - protecao em tempo real, nuvem, violacoes: 'Protecao contra virus e ameacas' >\n"
    "    'Gerenciar configuracoes';  firewall: 'Firewall e protecao de rede'.\n"
    "NOTIFICACOES DO WINDOWS DEFENDER — o caminho certo:\n"
    "  1) {\"acao\":\"abrir_config\",\"pagina\":\"seguranca do windows\"} abre o app Seguranca do Windows;\n"
    "  2) clique em 'Configuracoes' (engrenagem, embaixo a esquerda) e depois em 'Gerenciar notificacoes'.\n"
    "  NAO e a pagina de notificacoes do sistema (ms-settings:notifications): ela liga/desliga as\n"
    "  notificacoes de TODOS os programas — NAO mexa nela neste pedido.\n"
    "  A pagina 'Gerenciar notificacoes' tem 3 grupos, cada um com UM interruptor principal:\n"
    "   - protecao contra virus e ameacas: 'Receber notificacoes informativas' (+3 caixas);\n"
    "   - protecao de conta: 'Obter notificacoes de protecao de conta' (+2 caixas);\n"
    "   - firewall: 'Notificar-me quando o Microsoft Defender Firewall bloquear...' (+3 caixas:\n"
    "     dominio, particular, publico).\n"
    "  DESATIVAR TODAS = deixar os 3 interruptores principais DESLIGADOS (as caixas ficam cinza\n"
    "  sozinhas). ATIVAR TODAS = ligar os 3 e deixar todas as caixas MARCADAS. Um de cada vez,\n"
    "  olhando o estado [ligado]/[desligado] na lista antes de clicar.")


def _pedir_passo(relay, **kw):
    """Chama `relay.pedir_passo_na_tela` com os campos novos (v1.0.29) e, se a
    ponte for de antes deles (ou um duble de teste), repete no formato antigo.
    """
    try:
        return relay.pedir_passo_na_tela(**kw)
    except TypeError as exc:
        if "unexpected keyword" not in str(exc):
            raise
        for k in ("regras", "memoria", "historico_seq", "cancelar"):
            kw.pop(k, None)
        return relay.pedir_passo_na_tela(**kw)


# Uma conta de calculadora: numeros com pelo menos um operador entre eles.
_CONTA = re.compile(r"^\(?-?\d[\d.,]*\)?(\s*[-+*/x×÷^%]\s*\(?-?\d[\d.,]*\)?)+\s*=?$")


def _garantir_janela_da_foto(ctx: dict) -> str:
    """Antes de ESCREVER ou TECLAR: a janela da frente ainda e a da foto?

    Por que (23/09/2026, primeiro teste real da v1.0.29): a foto mostrou a
    Calculadora na frente, o modelo mandou digitar "125*8" — e o texto foi
    parar no YouTube, porque entre a foto e o teclado a janela da frente mudou
    (o dono clicou no video). Teclado sempre vai para quem esta na frente; se
    nao for a janela que o modelo viu, o texto cai no lugar errado — e numa
    janela de conversa isso e mensagem mandada para alguem.

    Se mudou, traz a janela da foto de volta. Devolve "" se esta tudo certo, ou
    o motivo de nao ter dado para escrever com seguranca.
    """
    esperado = ctx.get("hwnd_da_foto")
    if not esperado:
        return ""
    try:
        import win32gui  # noqa: PLC0415
        agora = win32gui.GetForegroundWindow()
        if agora == esperado or not win32gui.IsWindow(esperado):
            return ""
        titulo_agora = (win32gui.GetWindowText(agora) or "outra janela")[:50]
        if _trazer_para_frente(esperado):
            ctx["janela_trazida"] = titulo_agora
            return ""
        return (f"a janela da frente mudou desde a foto (agora e '{titulo_agora}') e nao "
                f"consegui trazer a certa de volta — nao escrevi nada")
    except Exception:
        return ""


def _janela_e_do_ted() -> bool:
    """A janela em primeiro plano e a do proprio JARVIS?"""
    try:
        import os  # noqa: PLC0415
        import win32gui  # noqa: PLC0415
        import win32process  # noqa: PLC0415
        h = win32gui.GetForegroundWindow()
        if not h:
            return False
        _tid, pid = win32process.GetWindowThreadProcessId(h)
        return pid == os.getpid()
    except Exception:
        return False


# Nome falado -> endereco que o Executar (Win+R) entende.
_PAGINA_PARA_ENDERECO = {
    "notificacoes": "ms-settings:notifications",
    "notificacao": "ms-settings:notifications",
    # 23/09/2026: o APP Seguranca do Windows direto. `ms-settings:windowsdefender`
    # abre uma pagina das Configuracoes que so tem o botao "Abrir Seguranca do
    # Windows" — um passo a mais, e ele se perdia ali.
    "windows defender": "windowsdefender:",
    "defender": "windowsdefender:",
    "seguranca do windows": "windowsdefender:",
    "seguranca": "windowsdefender:",
    "som": "ms-settings:sound", "audio": "ms-settings:sound",
    "tela": "ms-settings:display", "video": "ms-settings:display",
    "energia": "ms-settings:powersleep", "bateria": "ms-settings:batterysaver",
    "aplicativos": "ms-settings:appsfeatures", "apps": "ms-settings:appsfeatures",
    "inicializar": "ms-settings:startupapps",
    "programas que iniciam": "ms-settings:startupapps",
    "wifi": "ms-settings:network-wifi", "wi-fi": "ms-settings:network-wifi",
    "rede": "ms-settings:network-status",
    "bluetooth": "ms-settings:bluetooth",
    "privacidade": "ms-settings:privacy",
    "contas": "ms-settings:yourinfo", "conta": "ms-settings:yourinfo",
    "windows update": "ms-settings:windowsupdate",
    "atualizacao": "ms-settings:windowsupdate",
    "personalizacao": "ms-settings:personalization",
    "tema": "ms-settings:themes", "papel de parede": "ms-settings:personalization-background",
    # 23/09/2026: modo claro/escuro fica em CORES; sem isto ele abria "tema"
    # ou "personalizacao" e gastava passos procurando.
    "cores": "ms-settings:colors", "cor": "ms-settings:colors",
    "modo escuro": "ms-settings:colors", "modo claro": "ms-settings:colors",
    "modo de cor": "ms-settings:colors",
    "luz noturna": "ms-settings:nightlight", "modo de jogo": "ms-settings:gaming-gamemode",
    "barra de tarefas": "ms-settings:taskbar", "area de transferencia": "ms-settings:clipboard",
    "microfone": "ms-settings:privacy-microphone", "camera": "ms-settings:privacy-webcam",
    "localizacao": "ms-settings:privacy-location", "vpn": "ms-settings:network-vpn",
    "proxy": "ms-settings:network-proxy", "sobre": "ms-settings:about",
    "recuperacao": "ms-settings:recovery", "ativacao": "ms-settings:activation",
    "aplicativos padrao": "ms-settings:defaultapps", "fontes": "ms-settings:fonts",
    "mouse": "ms-settings:mousetouchpad", "teclado": "ms-settings:keyboard",
    "armazenamento": "ms-settings:storagesense",
    "idioma": "ms-settings:regionlanguage",
    "data e hora": "ms-settings:dateandtime", "hora": "ms-settings:dateandtime",
    "impressoras": "ms-settings:printers",
    "area de trabalho remota": "ms-settings:remotedesktop",
    "painel de controle": "control", "servicos": "services.msc",
    "gerenciador de dispositivos": "devmgmt.msc",
    "gerenciador de disco": "diskmgmt.msc",
    "programas instalados": "appwiz.cpl", "desinstalar": "appwiz.cpl",
    "conexoes de rede": "ncpa.cpl", "registro": "regedit",
    "configuracoes": "ms-settings:", "configuracao": "ms-settings:",
}


def _endereco_da_pagina(nome: str) -> str:
    """"notificacoes do Windows Defender" -> "ms-settings:windowsdefender"."""
    alvo = _sem_acento_simples(str(nome or "").lower().strip())
    if alvo.startswith(("ms-settings:", "shell:", "control", "windowsdefender:")):
        return str(nome).strip()
    if alvo in _PAGINA_PARA_ENDERECO:
        return _PAGINA_PARA_ENDERECO[alvo]
    # o mais especifico que couber dentro da frase ("notificacoes do defender"
    # tem de cair no defender, nao em notificacoes)
    achados = [(chave, end) for chave, end in _PAGINA_PARA_ENDERECO.items()
               if chave in alvo]
    if not achados:
        return ""
    return max(achados, key=lambda kv: len(kv[0]))[1]


def _executar(acao: dict, ctx: dict) -> str:
    """Faz UM movimento no PC. Devolve a linha que entra no historico.

    Marca `ctx["ultimo_ok"]` como False quando o movimento NAO aconteceu (alvo
    inexistente, janela errada, elemento que nao apareceu). E o que permite a
    receita parar no primeiro tropeco em vez de seguir digitando no vazio.
    """
    import pyautogui  # noqa: PLC0415
    # O FAIL-SAFE DO CANTO SAIU (04/09/2026), e o ESC entrou no lugar.
    #
    # O pyautogui aborta tudo se o mouse estiver num canto da tela quando um
    # movimento comeca. No primeiro teste ao vivo do JARVIS isso derrubou a tarefa
    # do meio para o fim: o dono tinha deixado o mouse parado no canto, e cada
    # passo virava erro — ate o Codex desistir e PEDIR que ele mexesse no
    # mouse (passo 11 do registro). Mouse parado no canto e posicao comum;
    # freio de emergencia nao pode disparar sozinho por causa dela.
    # Agora o freio e explicito, avisado por voz e na faixa: a tecla ESC.
    pyautogui.FAILSAFE = False
    pyautogui.PAUSE = 0.0
    ctx["ultimo_ok"] = True

    def falhou(motivo: str) -> str:
        ctx["ultimo_ok"] = False
        return motivo
    tipo = str(acao.get("acao") or "").lower().strip()

    # O ALVO SO E PROCURADO POR QUEM MIRA (04/09/2026).
    #
    # No motor do Bob esta linha rodava para TODOS os movimentos, antes de
    # olhar o tipo. Para `clicar_nome`, `expandir`, `acionar` e `rolar_ate` —
    # que procuram o elemento pelo nome por conta propria, logo abaixo — era uma
    # leitura inteira da arvore de acessibilidade (a parte mais cara da volta)
    # feita so para ser jogada fora. Quem nao mira nao paga.
    if tipo in _COM_ALVO:
        sx, sy, onde = _alvo(acao, ctx)
    else:
        sx, sy, onde = None, None, ""

    if tipo in ("clicar", "click", "clicar_duplo", "double_click",
                "clicar_direito", "right_click", "menu_direito",
                "mover", "move", "hover"):
        if sx is None:
            return falhou(f"nao consegui mirar ({onde or 'sem alvo'}) — nada feito")
        # CTRL/SHIFT JUNTO COM O CLIQUE: e assim que se escolhe VARIOS arquivos
        # de uma vez (ctrl+clique) ou um intervalo inteiro (shift+clique) antes
        # de copiar, arrastar ou apagar. Sem isto cada arquivo custava uma volta
        # inteira na VPS — 8 segundos por arquivo para montar uma selecao.
        mods = _modificadores(acao)
        pyautogui.moveTo(sx, sy, duration=0.18)
        ctx["mouse_esperado"] = (sx, sy)
        if tipo in ("mover", "move", "hover"):
            return f"mouse em cima de {onde}"
        for m in mods:
            pyautogui.keyDown(m)
        try:
            if tipo in ("clicar_duplo", "double_click"):
                pyautogui.click(sx, sy, clicks=2, interval=0.08)
                feito = f"cliquei duas vezes em {onde}"
            elif tipo in ("clicar_direito", "right_click", "menu_direito"):
                pyautogui.click(sx, sy, button="right")
                feito = f"cliquei com o botao direito em {onde} (o menu abriu; " \
                        f"os itens dele vem na lista do proximo passo)"
            else:
                pyautogui.click(sx, sy)
                feito = f"cliquei em {onde}"
        finally:
            for m in reversed(mods):
                pyautogui.keyUp(m)
        return feito + (f", segurando {'+'.join(mods)}" if mods else "")

    if tipo in ("arrastar", "drag", "arrastar_para", "soltar_em"):
        dx, dy, destino = _alvo(acao, ctx, ponta="2")
        if sx is None:
            return falhou(f"arrastar sem saber de onde ({onde or 'sem origem'}) — nada feito")
        if dx is None:
            return falhou(f"arrastar sem saber para onde ({destino or 'sem destino'}) — nada feito")
        _arrastar(sx, sy, dx, dy)
        ctx["mouse_esperado"] = (dx, dy)
        return f"arrastei {onde or 'a origem'} ate {destino or 'o destino'}"

    if tipo in ("digitar", "type", "escrever"):
        texto = str(acao.get("texto") or "")
        if not texto:
            return falhou("digitar sem texto — nada feito")
        if _janela_e_do_ted():
            # 03/09/2026, no teste com o cliente: a receita nao achou o campo,
            # seguiu assim mesmo e digitou a PLACA dentro do chat do proprio
            # Bob ("Voce: ORP8F38"). Digitar as cegas na janela errada nao e so
            # inutil: com `credencial` seria a senha do cliente indo parar num
            # lugar qualquer. Se a frente e o assistente, nao digita.
            return falhou("a janela do JARVIS estava na frente — nao digitei nada")
        # PEDIU UM ALVO QUE NAO EXISTE = FALHA, nao "digita onde estiver".
        # (04/09/2026) O alvo era a prova de que o Codex sabia ONDE escrever.
        # Sem ele o texto ia para o foco atual, que numa tela que mudou pode ser
        # qualquer coisa — a barra de endereco, a busca do Windows, outro campo
        # ja preenchido. Digitar sem alvo continua valendo quando o Codex NAO
        # pediu alvo nenhum (ele acabou de clicar no campo no movimento anterior).
        if sx is None and _pediu_alvo(acao):
            return falhou(f"nao digitei: {onde or 'o alvo que voce pediu nao esta na tela'}")
        if sx is None:
            # Sem alvo, o texto vai para quem estiver na frente: tem de ser a
            # janela que o modelo viu. Ver `_garantir_janela_da_foto`.
            motivo = _garantir_janela_da_foto(ctx)
            if motivo:
                return falhou(motivo)
        if sx is not None:
            pyautogui.click(sx, sy)
            ctx["mouse_esperado"] = (sx, sy)
            time.sleep(0.15)
        # ONDE ESTA O FOCO? (03/09/2026) No Simplo o Bob clicou em "Buscar
        # placas", o modal abriu com o campo VAZIO e sem foco, e o `ctrl+a` do
        # "limpar" selecionou A PAGINA INTEIRA — a tela toda azul da print, e a
        # placa colada em lugar nenhum. Agora o foco manda: ctrl+a so em campo
        # de texto, e digitar as cegas em cima de um botao vira falha, para a
        # receita parar e a visao resolver.
        foco = tela_visao.tipo_do_foco()
        if foco in ("botao", "item", "aba", "caixa", "opcao", "menu", "link", "imagem"):
            return falhou(f"o foco esta num {foco}, nao num campo de texto — nao digitei "
                          f"(clique no campo antes)")
        if acao.get("limpar") and foco == "campo":
            pyautogui.hotkey("ctrl", "a")
            time.sleep(0.05)
        _digitar(texto)
        # CONTA NA CALCULADORA JA SAI COM O ENTER (23/09/2026, teste real). Com
        # "12*12" digitado, a Calculadora mostra "12 x" em cima e "12" embaixo
        # — e o modelo leu isso como "falta o segundo numero", digitou de novo
        # e passou 13 passos clicando botao por botao. Com o Enter junto, o que
        # aparece e o resultado, sem margem para leitura errada.
        if (_CONTA.match(texto.strip())
                and re.search(r"calculadora|calculator", tela_visao.janela_em_frente()[1] or "", re.I)):
            time.sleep(0.1)
            pyautogui.press("enter")
            # Se o modelo mandou o Enter junto (a dupla digitar+Enter), o dele
            # NAO pode sair: Enter de novo na Calculadora repete a conta
            # (12*12 vira 144*12). Ver o `tecla`.
            ctx["enter_ja_apertado"] = True
            return (f"digitei {texto[:45]!r} na Calculadora e apertei Enter "
                    f"(o visor mostra o RESULTADO agora)")
        return f"digitei {texto[:45]!r}" + (f" em {onde}" if onde else "")

    if tipo in ("credencial", "senha", "login"):
        # A senha NUNCA sai deste computador: o Codex so pede pelo apelido.
        from actions.acessos import ler_credencial  # noqa: PLC0415
        if _janela_e_do_ted():
            return falhou("a janela do JARVIS estava na frente — nao preenchi nada")
        apelido = str(acao.get("apelido") or acao.get("site") or "").strip()
        campo = str(acao.get("campo") or "senha").strip().lower()
        valor = ler_credencial(apelido, campo)
        if not valor:
            return falhou(f"NAO TENHO a {campo} de '{apelido}' guardada — "
                          f"pergunte ao dono ou peca para ele salvar no JARVIS")
        # Senha com alvo errado e pior que texto com alvo errado: ela iria para
        # a tela inteira em texto claro. Mesma regra do `digitar`, sem excecao.
        if sx is None and _pediu_alvo(acao):
            return falhou(f"nao preenchi a senha: {onde or 'o campo que voce pediu nao esta na tela'}")
        if sx is not None:
            pyautogui.click(sx, sy)
            ctx["mouse_esperado"] = (sx, sy)
            time.sleep(0.15)
        _digitar(valor)
        return f"preenchi a {campo} de '{apelido}' (nao mostro o valor)"

    if tipo in ("tecla", "key", "atalho", "hotkey"):
        teclas = _mapa_teclas(acao.get("teclas") or acao.get("tecla") or acao.get("texto") or "")
        if not teclas:
            return falhou("tecla vazia — nada feito")
        # O PYAUTOGUI IGNORA NOME QUE NAO CONHECE, EM SILENCIO (04/09/2026).
        # `press("controle")` nao levanta erro: nao aperta nada e volta como se
        # tivesse apertado. O historico dizia "apertei controle+a" numa volta em
        # que nada aconteceu, e o Codex seguia achando que ja estava selecionado.
        desconhecidas = _teclas_conhecidas(teclas)
        if desconhecidas:
            return falhou(f"nao conheco a tecla {', '.join(desconhecidas)!r} — "
                          f"use o nome do teclado (enter, esc, tab, f2, delete, pgdn, "
                          f"ctrl+c...) ou digite o texto com 'digitar'")
        vezes = _inteiro(acao.get("vezes"), 1, 1, 30)
        if teclas == ["enter"] and ctx.pop("enter_ja_apertado", False):
            return "o Enter ja tinha sido apertado junto com a conta — nao apertei de novo"
        # Atalho de sistema (win+..., alt+tab) nao depende de quem esta na
        # frente; o resto (enter, ctrl+s, delete) vai para a janela da frente,
        # e tem de ser a da foto.
        if not any(t in ("win", "winleft", "winright") for t in teclas) and teclas != ["alt", "tab"]:
            motivo = _garantir_janela_da_foto(ctx)
            if motivo:
                return falhou(motivo.replace("nao escrevi nada", "nao apertei nada"))
        # ESC mandado por ELE MESMO (fechar menu, cancelar caixa) nao pode
        # acionar o freio do ESC — o Windows nao distingue tecla de gente de
        # tecla de programa. O laco avisa o freio antes de teclar.
        freio = ctx.get("freio_esc")
        if freio is not None and any(t == "esc" for t in teclas):
            freio.ignorar_por(0.35 + 0.12 * vezes)
        for _ in range(vezes):
            if len(teclas) == 1:
                pyautogui.press(teclas[0])
            else:
                pyautogui.hotkey(*teclas)
            time.sleep(0.04)
        sufixo = f" {vezes}x" if vezes > 1 else ""
        return f"apertei {'+'.join(teclas)}{sufixo}"

    if tipo in ("abrir_config", "abrir_configuracao", "executar", "win_r"):
        # O EXECUTAR EM VEZ DA BUSCA (07/09/2026, observacao do Diego):
        #
        #   "ao inves dele usar a pesquisa do Windows, que e aquela pesquisa
        #    simples, ele nao usa o Windows+R e digita o comando certinho pra
        #    exatamente o que o cliente ta pedindo? O Codex sabe todos os
        #    comandos do Windows pra ir exatamente aonde tem que ir"
        #
        # A busca do Iniciar devolve resultado PARECIDO e o Codex se perde nela:
        # num teste foram oito passos rodando em circulo entre a busca e a
        # Seguranca do Windows. Aqui a pagina e aberta pelo endereco, direto.
        pedido_pg = str(acao.get("pagina") or acao.get("nome")
                        or acao.get("comando") or acao.get("texto") or "").strip()
        endereco = _endereco_da_pagina(pedido_pg)
        if not endereco and pedido_pg:
            # O Executar tambem abre PROGRAMA ("notepad", "cmd", "mspaint.exe").
            # So nao vale mandar uma frase: "notificacoes do sistema" nao e
            # comando e viraria uma caixa de erro na cara do dono.
            uma_palavra = len(pedido_pg.split()) == 1
            if uma_palavra or pedido_pg.lower().endswith((".exe", ".msc", ".cpl")):
                endereco = pedido_pg
        if not endereco:
            return (f"nao conheco o endereco de '{pedido_pg[:60]}' — me diga um "
                    "endereco de verdade (ms-settings:..., control, services.msc) "
                    "ou abra pela janela mesmo")
        pyautogui.hotkey("win", "r")
        time.sleep(0.6)
        _digitar(endereco)
        time.sleep(0.2)
        pyautogui.press("enter")
        time.sleep(1.6)
        return f"abri pelo Executar: {endereco}"

    if tipo in ("rolar", "scroll"):
        # A RODA ROLA O QUE ESTA EMBAIXO DO MOUSE. No teste com o cliente o
        # Codex mandou rolar em (104, 645) — que e a barra lateral do Simplo,
        # nao o conteudo: o menu rolou, a lista de manuais nao, e o laco achou
        # que a tela tinha parado de reagir. Sem alvo explicito, agora o mouse
        # vai para o MEIO DO CONTEUDO (centro da tela) antes de rolar.
        direcao = _sem_acento_simples(str(acao.get("direcao") or "baixo"))
        quantidade = _inteiro(acao.get("quantidade"), 5, 1, 40)
        # ROLADA GRANDE PULA COISA (07/09/2026, olho do Diego):
        #
        #   "ele desceu todo o scroll pra baixo de uma vez, entao deve ter
        #    cortado; ao inves de descer por partes pra ir acompanhando"
        #
        # Ele mandou desativar TODAS as notificacoes, o Codex rolou 15 de uma
        # vez, e os aplicativos que passaram voando no meio ficaram ligados. O
        # ponto e simples: entre uma rolada e a proxima FOTO ele nao ve nada, e
        # o que nao foi visto nao e feito. Agora o teto por movimento e 5 (uma
        # tela, mais ou menos), e o recado explica que basta rolar de novo.
        TETO_POR_ROLADA = 5
        pediu = quantidade
        quantidade = min(quantidade, TETO_POR_ROLADA)
        if sx is None:
            larg, alt = pyautogui.size()
            sx, sy = larg // 2, alt // 2
        pyautogui.moveTo(sx, sy, duration=0.12)
        ctx["mouse_esperado"] = (sx, sy)
        # "DESCE MAIS" e o pedido que mais aparece (04/09/2026). Tres coisas
        # entraram por causa dele:
        #   - o teto subiu de 20 para 40 (uma pasta com 300 arquivos precisa);
        #   - `ate_o_fim`/`ate_o_topo` vao de uma vez, com ctrl+End/ctrl+Home,
        #     que e instantaneo e nao depende de quantas voltas de roda faltam;
        #   - esquerda/direita rolam de lado (shift+roda), que e o unico jeito
        #     de alcancar coluna escondida em planilha e em lista larga.
        if acao.get("ate_o_fim") or direcao in ("fim", "final", "fundo"):
            pyautogui.hotkey("ctrl", "end")
            time.sleep(0.3)
            return "fui para o FIM da lista (ctrl+End)"
        if acao.get("ate_o_topo") or direcao in ("topo", "inicio", "comeco"):
            pyautogui.hotkey("ctrl", "home")
            time.sleep(0.3)
            return "voltei para o TOPO da lista (ctrl+Home)"
        if direcao in ("esquerda", "left", "direita", "right"):
            sinal = 1 if direcao in ("esquerda", "left") else -1
            pyautogui.keyDown("shift")
            try:
                pyautogui.scroll(quantidade * sinal * 120)
            finally:
                pyautogui.keyUp("shift")
            return f"rolei {quantidade} para a {direcao} em ({sx},{sy})"
        sinal = 1 if direcao in ("cima", "up", "acima") else -1
        pyautogui.scroll(quantidade * sinal * 120)
        if pediu > quantidade:
            return (f"rolei so {quantidade} para {direcao} (voce pediu {pediu}) — "
                    f"rolada grande PULA conteudo, e o que voce nao ve na foto "
                    f"voce nao faz. Olhe esta tela, faca o que tem de ser feito "
                    f"nela, e ai role mais {TETO_POR_ROLADA} de cada vez ate o fim.")
        return f"rolei {quantidade} para {direcao} em ({sx},{sy})"

    if tipo in ("abrir_pasta", "abrir_diretorio", "explorar"):
        # ABRIR PASTA E SUBPASTA PELO CAMINHO (04/09/2026, pedido do Diego).
        #
        # Mesma ideia do `abrir_url`: determinismo vale mais que visao. Procurar
        # a pasta clicando no Explorador custa uma volta por nivel — tres voltas
        # (24 s) para chegar em Documentos\Clientes\AJ, e cada volta e uma
        # chance de clicar na pasta errada. Aqui vai direto, em 1 s.
        #
        # Aceita o nome que a pessoa usa ("downloads", "area de trabalho") e o
        # caminho completo, com ou sem subpasta.
        caminho = str(acao.get("caminho") or acao.get("nome") or acao.get("texto") or "").strip()
        if not caminho:
            return falhou("abrir_pasta sem caminho — nada feito")
        destino = _resolver_pasta(caminho)
        if destino is None:
            return falhou(f"nao achei a pasta {caminho!r} neste computador")
        antes = _janelas_visiveis()
        try:
            import os  # noqa: PLC0415
            os.startfile(destino)  # noqa: S606
        except Exception as exc:
            return falhou(f"nao consegui abrir a pasta: {str(exc)[:70]}")
        frente = _frente_para_o_que_abriu(antes, os.path.basename(str(destino).rstrip("\\/")), 3.0)
        _dormir(0.6, ctx)
        return f"abri a pasta {destino}{frente}"

    if tipo in ("abrir_url", "abrir_site", "url"):
        # Determinismo vale mais que visao: digitar a URL na barra e sempre
        # certo, enquanto procurar o icone do navegador na tela pode falhar.
        alvo_url = str(acao.get("url") or acao.get("texto") or "").strip()
        if not re.match(r"^https?://", alvo_url, re.I):
            if not re.match(r"^[\w.-]+\.[a-z]{2,}", alvo_url, re.I):
                return f"URL invalida: {alvo_url[:60]!r}"
            alvo_url = "https://" + alvo_url
        # SE O NAVEGADOR JA ESTA NA FRENTE, navega na ABA ATUAL (ctrl+L). O
        # `webbrowser.open` abre uma aba nova a cada chamada: no teste com o
        # cliente sobraram abas empilhadas, o Bob se perdeu entre elas e a tela
        # "ficou borrada" (era outra aba por cima). Aba nova so quando nao ha
        # navegador na frente.
        titulo_agora = tela_visao.janela_em_frente()[1].lower()
        if any(n in titulo_agora for n in ("chrome", "edge", "firefox", "opera", "brave")):
            pyautogui.hotkey("ctrl", "l")
            time.sleep(0.25)
            _digitar(alvo_url)
            time.sleep(0.15)
            pyautogui.press("enter")
            time.sleep(2.0)
            return f"naveguei nesta aba para {alvo_url[:70]}"
        import webbrowser  # noqa: PLC0415
        webbrowser.open(alvo_url)
        time.sleep(2.5)
        return f"abri {alvo_url[:80]}"

    if tipo in ("focar_janela", "focar", "janela"):
        titulo = str(acao.get("titulo") or acao.get("texto") or "").strip().lower()
        if not titulo:
            return "focar_janela sem titulo — nada feito"
        from actions.digitar_na_tela import _achar_janela  # noqa: PLC0415
        h = _achar_janela([titulo])
        if not h:
            # O titulo que ele chuta costuma ser o nome do programa com acento
            # ou pedaco ("calculadora" x "Calculadora"; "bloco" x "Sem titulo -
            # Bloco de Notas"): tenta sem acento, palavra por palavra.
            for termo in _sem_acento_simples(titulo).split():
                if len(termo) >= 4:
                    h = _achar_janela([termo])
                    if h:
                        break
        if not h:
            abertas = ", ".join(f"'{t[:40]}'" for t in list(_janelas_visiveis().values())[:8])
            return falhou(f"nao achei nenhuma janela com {titulo!r}. Abertas agora: {abertas}")
        ok = _trazer_para_frente(h)
        time.sleep(0.25)
        try:
            import win32gui  # noqa: PLC0415
            nome_janela = win32gui.GetWindowText(h)[:60]
        except Exception:
            nome_janela = titulo
        if not ok:
            return falhou(f"achei a janela '{nome_janela}', mas o Windows nao deixou por na frente")
        return f"trouxe para a frente a janela '{nome_janela}'"

    if tipo in ("abrir_programa", "abrir_app", "executar_programa"):
        # Abrir programa por NOME, sem procurar icone na tela. Vale para o que
        # o dono pediu de uso geral: Configuracoes, Painel de Controle,
        # Regedit, Gerenciador de Tarefas, Explorador, e qualquer .exe do PC.
        nome = str(acao.get("nome") or acao.get("texto") or acao.get("programa") or "").strip()
        if not nome:
            return falhou("abrir_programa sem nome — nada feito")
        atalhos = {
            "configuracoes": "ms-settings:", "configurações": "ms-settings:",
            "programas instalados": "ms-settings:appsfeatures",
            "aplicativos": "ms-settings:appsfeatures",
            "desinstalar programa": "appwiz.cpl", "adicionar ou remover programas": "appwiz.cpl",
            "painel de controle": "control", "registro": "regedit",
            "editor de registro": "regedit", "gerenciador de tarefas": "taskmgr",
            "gerenciador de dispositivos": "devmgmt.msc",
            "explorador": "explorer", "meu computador": "explorer",
        }
        alvo_prog = atalhos.get(_sem_acento_simples(nome), nome)
        from actions.open_app import open_app  # noqa: PLC0415
        antes = _janelas_visiveis()
        r = open_app({"app_name": alvo_prog})
        # NASCEU ATRAS? (23/09/2026) Programa aberto por processo de fundo
        # costuma nascer atras da janela da frente — a Calculadora atras do
        # YouTube em tela cheia. Acha a janela nova e poe na frente.
        frente = _frente_para_o_que_abriu(antes, nome, limite_s=4.0)
        _dormir(0.6, ctx)
        return f"abri {alvo_prog!r}: {str(r)[:70]}{frente}"

    if tipo in ("pesquisar", "pesquisar_web", "buscar_na_internet"):
        # O Codex pode PERGUNTAR a internet no meio do caminho ("como desinstalo
        # o programa X no Windows 11?"). A busca sai daqui, do PC, e a resposta
        # entra no historico do proximo passo — ele decide o que fazer com ela.
        pergunta = str(acao.get("texto") or acao.get("consulta") or acao.get("nome") or "").strip()
        if not pergunta:
            return falhou("pesquisar sem texto — nada feito")
        try:
            from actions.web_search import web_search  # noqa: PLC0415
            r = web_search({"query": pergunta})
        except Exception as exc:
            return falhou(f"a busca falhou: {str(exc)[:60]}")
        return f"pesquisei {pergunta[:40]!r} e achei: {str(r)[:600]}"

    if tipo in ("expandir", "abrir_item", "recolher", "fechar_item"):
        # A setinha que abre uma sanfona, um no de arvore ou uma lista. Vale em
        # qualquer programa do Windows, nao so no navegador.
        nome = str(acao.get("nome") or acao.get("texto") or "").strip()
        if not nome:
            return falhou(f"{tipo} sem nome — nada feito")
        abrir = tipo in ("expandir", "abrir_item")
        el = tela_visao.expandir(nome, abrir=abrir)
        if not el:
            return falhou(f"nao achei {nome!r} para {'abrir' if abrir else 'fechar'}")
        return f"{'abri' if abrir else 'fechei'} {el['nome']!r}"

    if tipo in ("acionar", "apertar", "invocar"):
        # Aperta pelo comando do Windows: funciona com o item coberto, fora da
        # area visivel ou quando o clique simplesmente nao pega.
        nome = str(acao.get("nome") or acao.get("texto") or "").strip()
        if not nome:
            return falhou("acionar sem nome — nada feito")
        el = tela_visao.acionar(nome)
        if not el:
            return falhou(f"nao consegui acionar {nome!r} (o programa nao oferece o comando)")
        return f"acionei {el['nome']!r} pelo comando do Windows"

    if tipo in ("rolar_ate", "rolar_ate_ver", "procurar_na_tela"):
        # Rolar ATE achar, em vez de rolar as cegas. Quem rola e o proprio
        # Windows/navegador, no container certo — resolve a lista interna do
        # Simplo, que a roda do mouse nao alcancava.
        nome = str(acao.get("nome") or acao.get("texto") or "").strip()
        if not nome:
            return falhou("rolar_ate sem nome — nada feito")
        el = tela_visao.rolar_ate_ver(nome)
        if not el:
            return falhou(f"rolei procurando {nome!r} e nao achei na tela")
        if acao.get("clicar"):
            pyautogui.moveTo(el["x"], el["y"], duration=0.18)
            pyautogui.click(el["x"], el["y"])
            ctx["mouse_esperado"] = (el["x"], el["y"])
            return f'rolei ate {el["nome"]!r} e cliquei'
        return f'rolei ate {el["nome"]!r} (agora esta visivel)'

    if tipo in ("clicar_nome", "clicar_texto"):
        # Clique por NOME, lido do Windows na hora: nao depende de a etiqueta
        # numerada existir nem de a tela ter ficado igual desde a foto. E o que
        # as receitas usam ("clique no botao ENTRAR").
        nome = str(acao.get("nome") or acao.get("texto") or "").strip()
        el = tela_visao.achar_por_nome(nome) if nome else None
        if not el and nome:
            # Nao esta a vista? Pode estar so rolado para fora. Rola ate ele.
            el = tela_visao.rolar_ate_ver(nome)
        if not el:
            return falhou(f"nao achei nada chamado {nome!r} na tela")
        pyautogui.moveTo(el["x"], el["y"], duration=0.18)
        ctx["mouse_esperado"] = (el["x"], el["y"])
        if str(acao.get("duplo") or "").lower() in ("1", "true", "sim"):
            pyautogui.click(el["x"], el["y"], clicks=2, interval=0.08)
        else:
            pyautogui.click(el["x"], el["y"])
        return f'cliquei em {el["tipo"]} "{el["nome"]}"'

    if tipo in ("esperar_elemento", "esperar_aparecer"):
        nome = str(acao.get("nome") or acao.get("texto") or "").strip()
        limite = min(30.0, max(1.0, _num(acao.get("segundos"), 12)))
        el = (tela_visao.esperar_elemento(nome, limite_s=limite,
                                          deve_parar=ctx.get("deve_parar"))
              if nome else None)
        if not el:
            return falhou(f'esperei {limite:.0f}s e {nome!r} nao apareceu')
        return f'{nome!r} apareceu'

    if tipo in ("esperar", "wait", "aguardar"):
        s = min(ESPERA_MAXIMA_S, max(0.2, _num(acao.get("segundos"), 1.5)))
        _dormir(s, ctx)
        return f"esperei {s:.1f}s"

    # ── LER, CHEGAR PERTO E ARRUMAR A JANELA (23/09/2026, v1.0.29) ─────────
    #
    # Tres coisas que um operador de verdade faz o tempo todo e o JARVIS nao
    # tinha como fazer:
    #   ler_tela  selecionar o texto em vez de ler a foto reduzida (o log da
    #             VPS tem "leia a pagina e me de o resumo" levando 18 passos);
    #   ampliar   chegar perto de letra miuda antes de responder um numero;
    #   janela    maximizar/encaixar/fechar sem cacar o botao de 12 px.
    if tipo in ("ler_tela", "ler", "ler_texto", "ler_pagina"):
        titulo = tela_visao.janela_em_frente()[1][:60]
        texto = tela_visao.texto_da_janela(limite=5200)
        if not texto:
            return falhou(f"nao consegui ler texto nenhum da janela '{titulo}' "
                          f"(programa sem acessibilidade ou imagem) — use ampliar na foto")
        return (f"LI O TEXTO da janela '{titulo}' ({len(texto)} letras, lido do Windows):\n"
                + texto)

    if tipo in ("escolher", "selecionar_opcao", "escolher_opcao"):
        # LISTA SUSPENSA PELO WINDOWS (23/09/2026). Ver
        # `tela_visao.escolher_na_lista`: 17 passos viraram 1 no teste do tema.
        opcao = str(acao.get("opcao") or acao.get("texto") or acao.get("valor") or "").strip()
        lista = str(acao.get("lista") or acao.get("nome") or "").strip()
        if not lista and acao.get("elemento") is not None:
            try:
                el = ctx["por_numero"].get(int(acao.get("elemento")))
            except (TypeError, ValueError):
                el = None
            lista = (el or {}).get("nome") or ""
        if not lista or not opcao:
            return falhou("escolher precisa da lista (nome ou elemento) e da opcao")
        ok, recado = tela_visao.escolher_na_lista(lista, opcao)
        return recado if ok else falhou(recado)

    if tipo in ("ampliar", "zoom", "aproximar"):
        try:
            x1, y1 = _num(acao.get("x")), _num(acao.get("y"))
            x2, y2 = _num(acao.get("x2")), _num(acao.get("y2"))
        except Exception:
            return falhou("ampliar precisa de x, y, x2, y2 (o retangulo na foto)")
        ex, ey = ctx.get("escala_x", 1.0), ctx.get("escala_y", 1.0)
        dx, dy = ctx.get("desloc_x", 0), ctx.get("desloc_y", 0)
        esq, dir_ = sorted((int(x1 * ex + dx), int(x2 * ex + dx)))
        topo, baixo = sorted((int(y1 * ey + dy), int(y2 * ey + dy)))
        if dir_ - esq < 24 or baixo - topo < 16:
            return falhou("o retangulo de ampliar e pequeno demais — pegue uma area maior")
        ctx["ampliar"] = (esq, topo, dir_, baixo)
        return ("a PROXIMA foto vem AMPLIADA (so esse retangulo). Nela, x,y valem "
                "na foto ampliada; depois ela volta ao normal")

    if tipo in ("janela", "maximizar", "minimizar", "restaurar", "fechar_janela"):
        modo = _sem_acento_simples(str(acao.get("modo") or tipo).lower().strip())
        modo = {"maximizar": "maximizar", "maximizada": "maximizar", "max": "maximizar",
                "minimizar": "minimizar", "restaurar": "restaurar",
                "esquerda": "esquerda", "direita": "direita",
                "fechar": "fechar", "fechar_janela": "fechar"}.get(modo, modo)
        h, titulo = tela_visao.janela_em_frente()
        if not h:
            return falhou("nao ha janela na frente")
        if _janela_e_do_ted() and modo in ("fechar", "minimizar"):
            return falhou("a janela da frente e o proprio JARVIS — nao mexi nela")
        try:
            import win32con  # noqa: PLC0415
            import win32gui  # noqa: PLC0415
            if modo == "maximizar":
                win32gui.ShowWindow(h, win32con.SW_MAXIMIZE)
            elif modo == "minimizar":
                win32gui.ShowWindow(h, win32con.SW_MINIMIZE)
            elif modo == "restaurar":
                win32gui.ShowWindow(h, win32con.SW_RESTORE)
            elif modo in ("esquerda", "direita"):
                pyautogui.hotkey("win", "left" if modo == "esquerda" else "right")
                time.sleep(0.4)
                # O Windows abre o "encaixe" com as outras janelas; Esc fecha
                # a sugestao sem desfazer a metade.
                freio = ctx.get("freio_esc")
                if freio is not None:
                    freio.ignorar_por(0.6)
                pyautogui.press("esc")
            elif modo == "fechar":
                # Igual ao X: o programa ainda pergunta se quer salvar.
                win32gui.PostMessage(h, win32con.WM_CLOSE, 0, 0)
            else:
                return falhou(f"modo de janela desconhecido {modo!r} — use maximizar, "
                              f"minimizar, restaurar, esquerda, direita ou fechar")
        except Exception as exc:
            return falhou(f"nao consegui {modo} a janela: {str(exc)[:60]}")
        time.sleep(0.35)
        return f"janela '{titulo[:50]}': {modo}"

    # FECHAR TODAS AS CAIXAS DE ERRO DE UMA VEZ (22/09/2026, pedido do Diego:
    # "ele clica, aparece outro, mas nao consegue fechar tudo"). Ver
    # `_fechar_caixas_de_erro`.
    if tipo in ("fechar_erros", "fechar_erro", "fechar_avisos", "limpar_erros"):
        quantas, ditos = _fechar_caixas_de_erro()
        if not quantas:
            return "nao havia caixa de erro aberta"
        resto = _caixas_de_erro()
        recado = f"fechei {quantas} caixa(s) de erro"
        if ditos:
            recado += ". Elas diziam: " + " // ".join(d[:160] for d in ditos[:3])
        if resto:
            recado += (f". AINDA sobraram {len(resto)}: elas nascem de novo "
                       f"enquanto a causa nao for consertada — conserte a causa "
                       f"primeiro e so depois feche")
        return recado

    # ── CONSERTAR WINDOWS E TRABALHO DE COMANDO, NAO DE MOUSE (22/09/2026) ──
    #
    # Pedido do Diego: "adiciona mais robustez pra ele corrigir bugs do Windows,
    # problemas de programas, programas que corrompem, corrigir problemas em
    # geral do Windows, de qualquer sistema".
    #
    # Ate aqui o computer use so sabia CLICAR. Isso resolve configuracao, e nao
    # resolve conserto: reparar arquivo de sistema, refazer a pilha de rede,
    # reiniciar um servico travado, limpar o cache do Windows Update, ver o log
    # de erro — nada disso tem botao. Tem comando, e o Codex conhece todos.
    #
    # Entao ele ganhou a mao: `{"acao":"comando","texto":"sfc /scannow"}`. O
    # que sai do comando VOLTA para ele no historico — e isso que transforma
    # "tentar" em "diagnosticar e consertar".
    #
    # Os freios, porque isto e poder de verdade:
    #   - a lista negra abaixo nunca roda, nem com autorizacao do dono;
    #   - o que mexe no sistema passa pela confirmacao por voz que ja existe
    #     (`_PERIGOSO` cobre formatar, apagar, desinstalar, parar servico);
    #   - teto de tempo por comando, para um `chkdsk` nao prender a tarefa;
    #   - a saida volta CORTADA (4.000 letras), senao entope o historico.
    if tipo in ("comando", "powershell", "cmd", "executar_comando", "rodar"):
        texto_cmd = str(acao.get("texto") or acao.get("comando")
                        or acao.get("cmd") or "").strip()
        if not texto_cmd:
            return falhou("comando vazio")
        if _COMANDO_PROIBIDO.search(texto_cmd):
            return falhou(
                f"NAO rodei {texto_cmd[:60]!r}: comando desta familia e proibido "
                f"neste programa (apagar disco/particao, formatar, desligar a "
                f"protecao do Windows, baixar e executar coisa da internet). "
                f"Resolva pelo caminho normal ou peca ao dono.")
        # VARRER O DISCO INTEIRO E TEMPO JOGADO FORA (22/09/2026). No teste do
        # Roblox ele mandou `where /r C:\ Qt5Widgets.dll` e
        # `Get-ChildItem 'C:\Program Files','C:\Program Files (x86)','D:\'
        # -Recurse`: cada um desses come de 30 s a varios minutos, costuma
        # estourar o tempo e voltar vazio. Foi metade da lentidao que o dono
        # reclamou ("quase 1 minuto para cada passo").
        #
        # Barrar custa ZERO e devolve na hora o caminho certo — em vez de
        # gastar meio minuto para nao achar nada.
        if _varre_o_disco(texto_cmd):
            return falhou(
                "NAO rodei: esse comando varre o disco inteiro e demora "
                "minutos, estourando o tempo. Procure nas pastas certas, que "
                "estao escritas no pedido desta tarefa (usuario, area de "
                "trabalho, AppData, Program Files). Se nao souber onde o "
                "programa esta, leia o alvo do atalho dele ou rode "
                "`Get-Process <nome> | Select-Object Path` com ele aberto.")
        # O TETO PADRAO CAIU DE 120 s PARA 45 s. Comando de diagnostico bom
        # responde em segundos; o que passa de 45 s quase sempre e busca larga
        # demais, e esperar nao melhora. Quem precisa de mais (sfc, DISM,
        # chkdsk) pede `segundos` explicitamente, e ai vai ate 600.
        limite_s = min(600.0, max(5.0, _num(acao.get("segundos"), 45)))
        como_admin = bool(acao.get("admin") or acao.get("como_admin"))
        saida, codigo = _rodar_comando(texto_cmd, limite_s, como_admin)
        ctx["ultimo_ok"] = (codigo == 0)
        cabeca = (f"rodei: {texto_cmd[:120]}\n"
                  f"codigo de saida: {codigo}\n")
        return cabeca + ("saida:\n" + saida if saida else "(sem saida)")

    return falhou(f"movimento desconhecido {tipo!r} — ignorado")


# O que NUNCA roda, nem com o dono autorizando: nao ha tarefa de suporte que
# precise disto, e o estrago nao tem volta.
#
# CUIDADO AO MEXER AQUI: `\bformat\b`, que foi a primeira versao, barrava
# `Format-Table` e `Format-List` — o jeito NORMAL de o PowerShell mostrar
# qualquer coisa. Um `Get-Service | Format-Table` ficava proibido, e o
# diagnostico morria antes de comecar. Formatar unidade tem forma propria
# (`format C:`, `Format-Volume`) e e ela que entra na lista.
# ── COMANDO QUE SAI VARRENDO DISCO (22/09/2026) ────────────────────────────
#
# Nao e perigoso — e LENTO, e lento estraga a tarefa do mesmo jeito. No teste
# do Roblox ele mandou `where /r C:\` e um `Get-ChildItem` com
# 'C:\Program Files','C:\Program Files (x86)','D:\' e -Recurse: minutos de
# espera para voltar vazio.
#
# A PRIMEIRA VERSAO DISTO ERA UM REGEX SO, E BARRAVA O COMANDO CERTO:
# `Get-ChildItem 'C:\Users\Diego\AppData\Local\Roblox' -Recurse` caia junto,
# porque o padrao via "C:\" e "-Recurse" na mesma linha e nao olhava QUE pasta
# era. O que separa uma busca boa de uma varredura nao e ter -Recurse: e a
# PROFUNDIDADE da pasta. `C:\`, `C:\Users` e `C:\Program Files` tem o disco
# inteiro embaixo; `...\AppData\Local\Roblox` tem uma pasta de programa.
_VARRE_SEM_RECURSE = re.compile(
    r"(where\s+/r\s+[a-z]:|\bdir\s+[a-z]:\\?\s+[^|\n]*/s\b)", re.I)

# Pastas rasas demais para varrer: ate este tanto de niveis, com -Recurse, e
# varredura de disco. 'C:\Users\Diego' = 2 niveis; 'C:\Users\Diego\AppData\
# Local\Roblox' = 5.
_FUNDO_MINIMO = 3
_CAMINHO_NO_COMANDO = re.compile(r"""['"]?([a-z]:\\[^'",|\s]*)['"]?""", re.I)


def _varre_o_disco(texto: str) -> bool:
    """O comando vai varrer meio disco? (lentidao, nao perigo)"""
    t = str(texto or "")
    if _VARRE_SEM_RECURSE.search(t):
        return True
    if not re.search(r"-recurse\b", t, re.I):
        return False
    for bruto in _CAMINHO_NO_COMANDO.findall(t):
        partes = [p for p in bruto.rstrip("\\").split("\\") if p]
        # partes[0] e a unidade ("C:"); o resto sao pastas.
        if len(partes) - 1 < _FUNDO_MINIMO:
            return True
    return False

_COMANDO_PROIBIDO = re.compile(
    r"(\bformat(\.com)?\s+[a-z]:|\bformat-volume\b|\bdiskpart\b|\bclean\s+all\b|"
    r"remove-partition|clear-disk|\bcipher\s+/w|\bfsutil\s+(file\s+)?setzerodata|"
    r"remove-item\s+[^|\n]*\b[cC]:\\\s*(\\\*)?\s*(-recurse|/s)|"
    r"\brd\s+/s\s+/q\s+[cC]:\\?\s*$|\bdel\s+/[fqs]*\s+[cC]:\\\*|"
    r"set-mppreference\s+-disablerealtimemonitoring\s+\$?true|"
    r"\bbcdedit\b[^\n]*\b(safeboot|nointegritychecks|testsigning)\b|"
    r"invoke-webrequest[^\n]*\|\s*(iex|invoke-expression)|"
    r"\bcurl\b[^\n]*\|\s*(iex|invoke-expression|cmd|powershell)|"
    r"\biex\s*\(\s*new-object\s+net\.webclient|"
    r"net\s+user\s+\S+\s+/(add|delete)|"
    r"reg\s+delete\s+HKLM\\?\s*$)", re.I)


def _cortar_saida(saida: str, teto: int) -> str:
    """Encurta a saida de um comando SEM perder as duas pontas.

    A PRIMEIRA VERSAO GUARDAVA SO O FIM, e isso custou um teste inteiro
    (22/09/2026). O raciocinio era "o resultado vem por ultimo" — verdade para
    um comando que calcula, mentira para um que LISTA. Numa listagem o que
    importa pode estar em qualquer linha, e no teste do Roblox o
    `Qt5Widgets.dll.QUEBRADO-PELO-TESTE` caia no meio de dezenas de Qt5*.dll:
    o corte pelo fim o jogava fora, e ele buscava de novo, e de novo.

    Agora ficam o COMECO e o FIM, com o aviso do que sumiu no meio — e o aviso
    diz para refinar a busca, que e o que resolve de verdade.
    """
    saida = str(saida or "")
    if len(saida) <= teto:
        return saida
    pedaco = max(200, (teto - 120) // 2)
    linhas = saida.splitlines()
    comeco, fim = saida[:pedaco], saida[-pedaco:]
    return (comeco.rsplit("\n", 1)[0]
            + f"\n[... cortei o meio: a saida tem {len(linhas)} linhas e "
              f"{len(saida)} letras. Se o que voce procura pode estar no meio, "
              f"refine o comando (filtre por nome, use Select-Object -First, "
              f"conte com Measure-Object) em vez de repetir a mesma busca ...]\n"
            + fim.split("\n", 1)[-1])


def _rodar_comando(texto: str, limite_s: float, como_admin: bool) -> tuple:
    """Roda um comando do Windows e devolve (saida cortada, codigo).

    O TAMANHO DA SAIDA IMPORTA MAIS DO QUE PARECE (22/09/2026). Ela vira uma
    linha do historico, e a VPS corta cada linha em `TETO_DA_LINHA_NA_VPS`
    caracteres. Se a saida passar disso, o FIM dela — que e onde esta o
    resultado — e o que se perde. Por isso o teto aqui e menor que o de la: o
    cabecalho ("rodei:", "codigo de saida:") tambem ocupa espaco.
    """
    saida_max = TETO_DA_LINHA_NA_VPS - 300
    try:
        if como_admin:
            # Elevar abre o UAC, e o laco ja sabe esperar o dono clicar em Sim.
            # `-Verb RunAs` nao devolve a saida, entao ela e gravada em arquivo
            # e lida depois — senao o Codex ficaria sem o resultado, que e a
            # parte que interessa.
            import tempfile  # noqa: PLC0415
            alvo = os.path.join(tempfile.gettempdir(), f"jarvis_cmd_{secrets.token_hex(6)}.txt")
            script = (f"$ErrorActionPreference='Continue'; "
                      f"& {{ {texto} }} *>&1 | Tee-Object -FilePath '{alvo}'")
            b64 = __import__("base64").b64encode(script.encode("utf-16-le")).decode()
            elevado = (f"Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden "
                       f"-ArgumentList '-NoProfile','-EncodedCommand','{b64}'")
            subprocess.run(["powershell", "-NoProfile", "-Command", elevado],
                           capture_output=True, text=True, timeout=limite_s,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                with open(alvo, "r", encoding="utf-8", errors="ignore") as f:
                    saida = f.read()
                os.remove(alvo)
            except Exception:
                saida = "(o comando rodou elevado; nao consegui ler a saida)"
            return saida[-saida_max:].strip(), 0
        r = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", texto],
            capture_output=True, text=True, timeout=limite_s,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        saida = ((r.stdout or "") + (("\n" + r.stderr) if r.stderr else "")).strip()
        return _cortar_saida(saida, saida_max), int(r.returncode or 0)
    except subprocess.TimeoutExpired:
        return (f"o comando passou de {limite_s:.0f}s e eu o encerrei. Se ele "
                f"for demorado de verdade (sfc, DISM, chkdsk), rode de novo com "
                f'"segundos":600.'), 1
    except Exception as exc:
        return f"nao consegui rodar: {type(exc).__name__}: {exc}", 1


# ── o laco ─────────────────────────────────────────────────────────────────

# ── OS ENDERECOS DO WINDOWS, PARA IR DIRETO (07/09/2026) ─────────────────
#
# Observacao do Diego, e ela vale: *"ao inves dele usar a pesquisa do Windows,
# que e aquela pesquisa simples, ele nao usa o Windows+R e digita o comando
# certinho pra exatamente o que o cliente ta pedindo?"*. No teste dele a busca
# do menu Iniciar levou o Codex a rodar em circulo por oito passos entre a
# pesquisa e a Seguranca do Windows; quando ele finalmente usou
# `ms-settings:windowsdefender` caiu na pagina certa de primeira.
#
# O Codex sabe esses enderecos, mas so lembra deles se estiverem na frente
# dele — entao vao no historico, escritos, em toda tarefa.
_ENDERECOS_DO_WINDOWS = (
    "   notificacoes ......... ms-settings:notifications\n"
    "   Windows Defender ..... ms-settings:windowsdefender\n"
    "   som .................. ms-settings:sound\n"
    "   video/tela ........... ms-settings:display\n"
    "   energia .............. ms-settings:powersleep\n"
    "   aplicativos .......... ms-settings:appsfeatures\n"
    "   inicializar com o PC . ms-settings:startupapps\n"
    "   wi-fi ................ ms-settings:network-wifi\n"
    "   bluetooth ............ ms-settings:bluetooth\n"
    "   privacidade .......... ms-settings:privacy\n"
    "   contas ............... ms-settings:yourinfo\n"
    "   Windows Update ....... ms-settings:windowsupdate\n"
    "   tema/personalizacao .. ms-settings:personalization\n"
    "   mouse ................ ms-settings:mousetouchpad\n"
    "   teclado .............. ms-settings:keyboard\n"
    "   armazenamento ........ ms-settings:storagesense\n"
    "   idioma ............... ms-settings:regionlanguage\n"
    "   data e hora .......... ms-settings:dateandtime\n"
    "   Seguranca do Windows . windowsdefender:\n"
    "   painel de controle ... control\n"
    "   servicos ............. services.msc\n"
    "   gerenciador de disco . diskmgmt.msc\n"
    "   programas instalados . appwiz.cpl\n"
    "   configuracao de rede . ncpa.cpl\n"
    "   registro ............. regedit")


# ── CONSERTAR O WINDOWS: A CAIXA DE FERRAMENTAS (22/09/2026) ───────────────
#
# Pedido do Diego: "as vezes o Windows ta com algum problema, o cliente pede pra
# ele corrigir e ele nao consegue. Adiciona mais robustez pra ele corrigir bugs
# do Windows, problemas de programas, programas que corrompem".
#
# O que faltava nao era inteligencia: era MAO. Ele so sabia clicar, e conserto
# de sistema nao tem botao. Agora existe a acao `comando` (ver `_executar`), e
# este texto e o que ensina o Codex a usa-la. Vai no historico de toda tarefa
# de conserto, porque o Codex conhece esses comandos mas so lembra deles quando
# estao escritos na frente dele — mesma licao dos enderecos `ms-settings:`.
# CADA PEDACO TEM DE CABER EM 1800 CARACTERES, QUE E O TETO DA VPS POR LINHA
# DE HISTORICO (`jarvis-olhar.js`: `String(h).slice(0, 1800)`). Ate 22/09/2026
# esse teto era 200 — e foi por isso que manual nenhum colou: chegava cortado
# na terceira linha, e a SAIDA DOS COMANDOS nunca chegava (o cabecalho
# "rodei:/codigo de saida:" ja comia os 200). Agora que cabe, o tamanho custa
# TEMPO: cada caractere daqui entra no pedido de TODO passo, e no teste de
# 22/09 o passo subiu de ~5,5 s para ~7,6 s quando o pedido engordou.
#
# Por isso o manual foi PARTIDO, e cada tarefa recebe so o pedaco que serve.
# Quem mexer aqui: pedaco novo tem de continuar abaixo de 1800 — o
# `_teste_manual_cabe.py` reprova se passar.
TETO_DA_LINHA_NA_VPS = 1800

_MANUAL_DE_REPARO = (
    "COMO SE CONSERTA WINDOWS (voce TEM a mao para isso nesta tarefa):\n"
    "Alem de clicar, voce pode rodar comando e LER A SAIDA:\n"
    '  {"acao":"comando","texto":"ipconfig /flushdns"}\n'
    '  {"acao":"comando","texto":"sfc /scannow","admin":true,"segundos":600}\n'
    "A saida do comando volta para voce no historico do passo seguinte. USE-A: "
    "diagnosticar e depois consertar vale MUITO mais que sair clicando.\n"
    "`admin:true` abre a tela de permissao do Windows e o dono clica em Sim; so "
    "use quando o comando precisar mesmo (reparo de sistema, servico, update).\n"
    "\n"
    "PRIMEIRO OLHE, DEPOIS MEXA. O caminho que funciona e sempre este:\n"
    "  1) veja o estado (Get-Service, Get-WinEvent, Get-PSDrive, Get-Process);\n"
    "  2) conserte a causa;\n"
    "  3) veja de novo, e so entao diga que terminou.\n"
    "\n"
    "PROGRAMA QUE NAO ABRE — LEIA O ERRO ANTES DE QUALQUER COISA.\n"
    "A mensagem na tela JA DIZ o que falta. Nao saia clicando, nao va ao site\n"
    "do fabricante, nao abra 'Programas instalados' antes de ler. Faca assim:\n"
    "  1) Abra o programa e LEIA a caixa de erro palavra por palavra. Diga no\n"
    "     seu proximo movimento o que ela diz — se voce nao consegue citar a\n"
    "     mensagem, voce ainda nao olhou.\n"
    "  2) A mensagem fala em ARQUIVO ou .dll que 'nao foi encontrado'? Entao o\n"
    "     conserto e o ARQUIVO, nao o programa inteiro. Ache a pasta do\n"
    "     programa e procure por ele, inclusive renomeado:\n"
    '     {"acao":"comando","texto":"Get-Process | Where-Object {$_.Name -like '
    "'*roblox*'} | Select-Object Name,Path\"}\n"
    '     {"acao":"comando","texto":"Get-ChildItem \\"<pasta do programa>\\" '
    '-Recurse -Filter \\"NOME.dll*\\" | Select-Object FullName,Length"}\n'
    "     Achou 'NOME.dll.alguma-coisa' ou 'NOME.dll.bak'? O arquivo esta la,\n"
    "     so com o nome errado. Renomeie de volta e acabou:\n"
    '     {"acao":"comando","texto":"Rename-Item \\"...\\NOME.dll.bak\\" '
    '\\"NOME.dll\\""}\n'
    "  3) Nao achou o arquivo em lugar nenhum? Ai sim: reparar/reinstalar o\n"
    "     programa (Programas instalados -> Modificar/Reparar), ou o pacote\n"
    "     que traz aquela peca (msvcp140.dll/vcruntime -> Visual C++\n"
    "     Redistributable; api-ms-win-crt -> atualizacao do Windows).\n"
    "  4) Onde ficam os programas, para procurar: C:\\Program Files,\n"
    "     C:\\Program Files (x86), %LOCALAPPDATA% e %APPDATA%. O caminho exato\n"
    "     do atalho sai com:\n"
    '     {"acao":"comando","texto":"$s=New-Object -ComObject WScript.Shell; '
    "Get-ChildItem \\\"$env:USERPROFILE\\\\Desktop\\\",\\\"$env:APPDATA\\\\Microsoft\\\\"
    "Windows\\\\Start Menu\\\" -Filter '*.lnk' -Recurse | ForEach-Object "
    '{$s.CreateShortcut($_.FullName).TargetPath}"}\n'
    "  5) DEPOIS de consertar, ABRA o programa e confira que ele abriu. Sem\n"
    "     isso a tarefa nao esta terminada.\n"
    "\n"
    "RECEITAS QUE RESOLVEM A MAIOR PARTE DAS QUEIXAS:\n"
    "  arquivo de sistema corrompido, telas de erro, coisa que nao abre:\n"
    '     sfc /scannow    e, se ele nao consertar tudo:\n'
    '     DISM /Online /Cleanup-Image /RestoreHealth   (os dois com admin, 600s)\n'
    "  internet caiu / so nele nao abre site:\n"
    "     ipconfig /flushdns ; ipconfig /release ; ipconfig /renew ;\n"
    "     netsh winsock reset ; netsh int ip reset    (admin; pede reinicio)\n"
    "  Windows Update travado:\n"
    "     net stop wuauserv ; net stop bits ; apagar o conteudo de\n"
    "     C:\\Windows\\SoftwareDistribution\\Download ; net start wuauserv ;\n"
    "     net start bits     (admin)\n"
    "  impressora que nao imprime: Restart-Service Spooler e limpar a fila em\n"
    "     C:\\Windows\\System32\\spool\\PRINTERS  (admin)\n"
    "  programa da Microsoft Store corrompido (Calculadora, Fotos, Loja):\n"
    "     Get-AppxPackage <nome> | Reset-AppxPackage       e, se nao bastar,\n"
    "     Get-AppxPackage <nome> | Foreach {Add-AppxPackage -DisableDevelopment"
    "Mode -Register \"$($_.InstallLocation)\\AppXManifest.xml\"}\n"
    "  a Loja em si: wsreset.exe\n"
    "  programa travado: Get-Process <nome> | Stop-Process -Force\n"
    "  disco cheio: cleanmgr /sagerun:1, ou apagar %TEMP% e\n"
    "     C:\\Windows\\Temp; ver o que ocupa com Get-ChildItem -Recurse\n"
    "  PC lento na abertura: Get-CimInstance Win32_StartupCommand, e desligar\n"
    "     em ms-settings:startupapps\n"
    "  disco com defeito: chkdsk C: /scan  (le sem travar o disco; /f so com o\n"
    "     dono avisado de que exige reiniciar)\n"
    "  driver com problema: Get-PnpDevice -Status Error, e devmgmt.msc\n"
    "  ver o que deu errado de verdade, com hora e mensagem:\n"
    "     Get-WinEvent -FilterHashtable @{LogName='System';Level=1,2;"
    "StartTime=(Get-Date).AddDays(-2)} -MaxEvents 30 | Format-List TimeCreated,"
    "ProviderName,Message\n"
    "     (troque System por Application para erro de PROGRAMA)\n"
    "  data/hora erradas quebrando site e login:\n"
    "     w32tm /resync  (admin), e ms-settings:dateandtime\n"
    "  ponto de restauracao, quando nada resolve: rstrui.exe (o dono escolhe)\n"
    "\n"
    "O QUE VOCE NAO FAZ, nem que o texto do dono peca: formatar, apagar disco "
    "ou particao, desligar a protecao do Windows, criar usuario, baixar e "
    "executar coisa da internet. Eu bloqueio esses comandos de qualquer jeito. "
    "Se for o unico caminho, explique ao dono com `responder` e pare.\n"
    "REINICIAR O PC: nunca por conta propria. Diga ao dono que precisa "
    "reiniciar e deixe que ELE reinicie.")

# ── ELE INVENTOU O NOME DO USUARIO (22/09/2026, 3o teste do Roblox) ────────
#
# No log, do passo 2 ao 14, todo comando procurava em:
#
#     C:\Users\Administrator\AppData\Local\Roblox
#     C:\Users\Administrator\Desktop
#
# O dono desta maquina e o DIEGO. Nenhuma dessas pastas existe, todo comando
# voltou vazio, e no fim ele disse "nao foi possivel localizar a instalacao" —
# depois de 5 minutos procurando no lugar errado.
#
# O mesmo defeito ja tinha acontecido em 05/09 no cerebro de CONVERSA ("ele
# montou C:\Users\Edwin\Desktop"), e foi consertado la, no
# `cerebro_codex._pastas_de_verdade`. O computer use nunca recebeu essa
# informacao: ele ve a TELA, e a tela nao diz quem e o dono da maquina.
#
# Agora diz. Vai no historico fixo de TODA tarefa, nao so nas de conserto:
# qualquer tarefa que mexa em arquivo tropeca no mesmo buraco.

# ── A INVESTIGACAO QUE O PROGRAMA FAZ SOZINHO (22/09/2026) ─────────────────
#
# Pedido do Diego depois do 5o teste do Roblox: "ele ta demorando muito, fica
# varios passos sem clicar nem mexer em nada... deixe mais robusto e mais
# rapido isso".
#
# O log explica o desperdicio: SESSENTA passos, quase todos buscando o mesmo
# arquivo com variacoes de `Get-ChildItem`. E a maior parte nem podia achar —
# ele usava `-Filter Qt5Widgets.dll`, nome EXATO, enquanto o arquivo no disco
# estava renomeado. Medido aqui depois: o filtro exato leva 1,2 s e nao acha;
# `-Filter Qt5Widgets.dll*` leva os mesmos 1,2 s e ACHA.
#
# Nada disso e trabalho de modelo. Quando a tela diz "falta X.dll", a
# investigacao inteira e mecanica: ler o nome do arquivo, achar a pasta do
# programa, procurar o arquivo la (inclusive renomeado). O programa faz isso em
# menos de 2 s, ANTES do primeiro passo, e entrega o resultado pronto — o Codex
# comeca sabendo onde esta o problema, em vez de descobrir em 60 voltas.

# "porque Qt5Widgets.dll nao foi encontrado", "MSVCP140.dll is missing"...
# O `.{0,40}?` no meio (e nao `[^A-Za-z0-9]`) e de proposito: em ingles o
# Windows escreve "MSVCP140.dll WAS not found", com palavra no meio, e a
# primeira versao desta regra nao casava com isso.
_ARQUIVO_QUE_FALTA = re.compile(
    r"([A-Za-z0-9_.\-]+\.(?:dll|exe|ocx|sys|so))"
    r".{0,40}?"
    r"(?:nao foi encontrad|n[aã]o foi encontrad|not found|is missing|"
    r"esta faltando|est[aá] faltando|ausente|missing|nao existe|"
    r"n[aã]o existe|could not be found|cannot find)", re.I)

# O programa de quem e a caixa de erro: "RobloxStudioBeta.exe - Erro do sistema"
_DONO_DO_ERRO = re.compile(r"([A-Za-z0-9_.\-]+\.exe)\s*[-–—]\s*", re.I)


# ── AS CAIXAS DE ERRO DO WINDOWS (22/09/2026) ──────────────────────────────
#
# Duas queixas do Diego, no mesmo teste, e as duas saem daqui:
#
#   "ele fica muito tempo tentando fechar esses erros e nao consegue; os erros
#    do Windows geralmente tem VARIOS erros, ele clica, aparece outro, mas nao
#    consegue fechar tudo"
#   "parece que todos os passos nao chegam completos para o Codex na VPS"
#
# Ele estava certo nas duas. Medido aqui com a caixa do Roblox aberta: a
# varredura de tela (`tela_visao.olhar`) traz os elementos da janela EM
# PRIMEIRO PLANO. A caixa de erro aparece na lista de janelas — mas o TEXTO
# dela, "porque Qt5Widgets.dll nao foi encontrado", NAO vai em elemento nenhum.
# O Codex so tinha a foto para ler aquilo, e foto ele le mal.
#
# Uma caixa de erro do Windows e uma janela de dialogo separada, com os textos
# dentro dela. Aqui elas sao lidas pelo proprio Windows (EnumWindows +
# EnumChildWindows), com texto exato — e fechadas do mesmo jeito, todas de uma
# vez, em vez de clicar em "OK" uma por uma e ver outra aparecer.

_TITULO_DE_CAIXA = re.compile(
    r"(erro|error|aviso|warning|falha|fail|problema|nao (pode|foi)|"
    r"cannot|unable|missing)", re.I)


def _janelas_de_dialogo() -> list:
    """As caixas de dialogo visiveis, com o texto de dentro. [(hwnd, titulo, texto)]"""
    try:
        import ctypes  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415
    except Exception:
        return []
    try:
        u32 = ctypes.WinDLL("user32", use_last_error=True)
        PROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

        def texto_de(h) -> str:
            n = u32.GetWindowTextLengthW(h)
            if n <= 0:
                return ""
            buf = ctypes.create_unicode_buffer(n + 1)
            u32.GetWindowTextW(h, buf, n + 1)
            return buf.value or ""

        def classe_de(h) -> str:
            buf = ctypes.create_unicode_buffer(64)
            u32.GetClassNameW(h, buf, 64)
            return buf.value or ""

        achadas = []

        def visita(h, _):
            try:
                if not u32.IsWindowVisible(h):
                    return True
                titulo = texto_de(h)
                if not titulo:
                    return True
                # `#32770` e a classe das caixas de dialogo do Windows. E o que
                # separa um alerta de uma janela de programa qualquer.
                if classe_de(h) != "#32770" and not _TITULO_DE_CAIXA.search(titulo):
                    return True
                partes = []

                def filho(c, _p):
                    t = texto_de(c)
                    if t:
                        partes.append(t)
                    return True

                u32.EnumChildWindows(h, PROC(filho), 0)
                achadas.append((int(h), titulo, " | ".join(partes)[:600]))
            except Exception:
                pass
            return True

        u32.EnumWindows(PROC(visita), 0)
        return achadas
    except Exception:
        return []


def _caixas_de_erro() -> list:
    """So as caixas que sao ERRO/AVISO — nao qualquer dialogo (salvar, abrir)."""
    fora = []
    for hwnd, titulo, texto in _janelas_de_dialogo():
        if _TITULO_DE_CAIXA.search(titulo) or _TITULO_DE_CAIXA.search(texto):
            fora.append((hwnd, titulo, texto))
    return fora


def _fechar_caixas_de_erro(log=None, voltas: int = 4) -> tuple:
    """Fecha TODAS as caixas de erro abertas. Devolve (quantas, o que diziam).

    Em voltas, porque fechar uma faz a proxima aparecer — foi exatamente a
    queixa do dono. Quatro voltas cobrem a pilha que vimos; se ainda sobrar,
    o laco normal tenta de novo no passo seguinte.
    """
    WM_CLOSE = 0x0010
    fechadas, ditos = 0, []
    try:
        import ctypes  # noqa: PLC0415
        u32 = ctypes.WinDLL("user32", use_last_error=True)
    except Exception:
        return 0, []
    for _ in range(max(1, voltas)):
        caixas = _caixas_de_erro()
        if not caixas:
            break
        for hwnd, titulo, texto in caixas:
            recado = f"{titulo}: {texto}".strip(": ")
            if recado and recado not in ditos:
                ditos.append(recado)
            try:
                u32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
                fechadas += 1
            except Exception:
                pass
        time.sleep(0.45)      # o Windows precisa de um instante para fechar
    if log and fechadas:
        log(f"[usar_computador] fechei {fechadas} caixa(s) de erro do Windows")
    return fechadas, ditos


def _achar_arquivo_perto(exe: str, arquivo: str) -> list:
    """Procura `arquivo` (e versoes renomeadas dele) na pasta do programa."""
    if not exe or not arquivo:
        return []
    base = os.path.dirname(exe)
    if not base or not os.path.isdir(base):
        return []
    achados = []
    raiz = os.path.splitext(arquivo)[0]
    try:
        # A pasta do proprio programa primeiro: e onde ele sempre esta.
        for nome in os.listdir(base):
            if nome.lower().startswith(raiz.lower()):
                achados.append(os.path.join(base, nome))
        # E um nivel acima, nas pastas irmas (o Roblox guarda uma pasta por
        # versao, e o arquivo pode estar na versao vizinha).
        acima = os.path.dirname(base)
        if acima and os.path.isdir(acima):
            for irma in os.listdir(acima):
                caminho = os.path.join(acima, irma)
                if caminho == base or not os.path.isdir(caminho):
                    continue
                try:
                    for nome in os.listdir(caminho):
                        if nome.lower().startswith(raiz.lower()):
                            achados.append(os.path.join(caminho, nome))
                except Exception:
                    continue
    except Exception:
        pass
    return achados[:12]


def _exe_do_erro(olhada: dict) -> str:
    """O caminho do programa que deu erro, a partir do que esta na tela."""
    nomes = [str(olhada.get("titulo") or "")]
    nomes += [str(j) for j in (olhada.get("janelas") or [])]
    alvo = ""
    for texto in nomes:
        achado = _DONO_DO_ERRO.search(texto)
        if achado:
            alvo = achado.group(1)
            break
    if not alvo:
        return ""
    # 1) O processo, se ele ainda estiver de pe: e a resposta exata.
    try:
        saida, _ = _rodar_comando(
            f"Get-Process -Name '{os.path.splitext(alvo)[0]}' -ErrorAction "
            f"SilentlyContinue | Select-Object -First 1 -ExpandProperty Path", 20, False)
        caminho = saida.strip().splitlines()[0].strip() if saida.strip() else ""
        if caminho and os.path.isfile(caminho):
            return caminho
    except Exception:
        pass
    # 2) Os atalhos do dono — area de trabalho e menu Iniciar.
    try:
        saida, _ = _rodar_comando(
            "$w=New-Object -ComObject WScript.Shell; "
            "@(\"$env:USERPROFILE\\Desktop\", "
            "\"$env:APPDATA\\Microsoft\\Windows\\Start Menu\") | "
            "ForEach-Object { Get-ChildItem $_ -Filter *.lnk -Recurse "
            "-ErrorAction SilentlyContinue } | ForEach-Object "
            "{ $w.CreateShortcut($_.FullName).TargetPath }", 30, False)
        for linha in saida.splitlines():
            linha = linha.strip()
            if linha.lower().endswith(alvo.lower()) and os.path.isfile(linha):
                return linha
    except Exception:
        pass
    return ""


def _investigar_erro_da_tela(olhada: dict, log) -> str:
    """O diagnostico pronto, ou vazio quando nao ha erro de arquivo na tela."""
    try:
        textos = [str(olhada.get("titulo") or "")]
        textos += [str(j) for j in (olhada.get("janelas") or [])]
        textos += [str(e.get("nome") or "") for e in (olhada.get("elementos") or [])[:60]]
        # O TEXTO DE DENTRO DAS CAIXAS DE ERRO. A varredura de tela nao o traz
        # (ela le a janela em primeiro plano), e e justamente ali que esta o
        # nome do arquivo que falta. Ver `_janelas_de_dialogo`.
        textos += [f"{t} {c}" for _h, t, c in _caixas_de_erro()]
        faltando = ""
        for texto in textos:
            achado = _ARQUIVO_QUE_FALTA.search(texto)
            if achado:
                faltando = achado.group(1)
                break
        if not faltando:
            return ""

        exe = _exe_do_erro(olhada)
        linhas = [f"EU JA INVESTIGUEI PARA VOCE, antes de voce comecar. Nao "
                  f"repita esta busca:",
                  f"  a tela diz que falta o arquivo: {faltando}"]
        if exe:
            linhas.append(f"  o programa que deu erro e: {exe}")
        achados = _achar_arquivo_perto(exe, faltando)
        if achados:
            linhas.append("  procurei na pasta desse programa e nas vizinhas, e achei:")
            for caminho in achados:
                try:
                    tam = os.path.getsize(caminho)
                except Exception:
                    tam = 0
                nome = os.path.basename(caminho)
                marca = ""
                if nome.lower() != faltando.lower():
                    marca = "   <-- E O ARQUIVO, SO QUE COM O NOME TROCADO"
                linhas.append(f"    {caminho}  ({tam} bytes){marca}")
            trocado = [c for c in achados
                       if os.path.basename(c).lower() != faltando.lower()]
            certo = [c for c in achados
                     if os.path.basename(c).lower() == faltando.lower()]
            if trocado and not certo:
                alvo = os.path.join(os.path.dirname(trocado[0]), faltando)
                linhas.append(
                    f"  ENTAO O CONSERTO E RENOMEAR DE VOLTA, e so isso. Faca "
                    f"agora, num movimento so:")
                linhas.append(
                    f'    {{"acao":"comando","texto":"Rename-Item -LiteralPath '
                    f"'{trocado[0]}' -NewName '{faltando}'\"}}")
                linhas.append(
                    f"  Depois confira com Test-Path '{alvo}' e ABRA o programa "
                    f"para ver se ele abre. NAO reinstale, NAO baixe nada.")
        elif exe:
            linhas.append(f"  procurei {faltando} na pasta do programa e nas "
                          f"vizinhas e NAO achei em lugar nenhum. Ai sim o "
                          f"caminho e repor a peca (reparar a instalacao, ou o "
                          f"pacote que traz esse arquivo).")
        else:
            linhas.append("  nao consegui descobrir sozinho onde o programa "
                          "esta instalado; descubra e procure o arquivo la.")
        texto_final = "\n".join(linhas)
        log(f"[usar_computador] investiguei antes: falta {faltando}; "
            f"{len(achados)} pista(s) na pasta do programa")
        return texto_final
    except Exception as exc:
        log(f"[usar_computador] investigacao previa falhou: {str(exc)[:90]}")
        return ""


def _pergunta_clara(acao: dict, fala: str, familia: str) -> str:
    """A pergunta de confirmacao, dizendo O QUE vai ser feito.

    O dono responde de ouvido, no meio de outra coisa. Ele precisa ouvir a
    acao, nao "esse passo".
    """
    tipo = str(acao.get("acao") or "").lower().strip()
    fala = str(fala or "").strip()

    if tipo in ("comando", "powershell", "cmd", "executar_comando", "rodar"):
        texto = str(acao.get("texto") or acao.get("comando") or "").strip()
        # O nome do programa/servico costuma ser a segunda palavra do comando.
        curto = re.sub(r"\s+", " ", texto)[:90]
        return (f"Preciso rodar um comando no seu computador: {curto}. "
                f"Posso? E so dizer sim ou nao.")

    # A FALA DO MODELO MANDA, quando existe: ela descreve a acao ("apagar os
    # arquivos temporarios"), enquanto o `nome` e so o rotulo do botao
    # ("Excluir") — e "Vou APAGAR Excluir" nao e portugues.
    alvo = str(acao.get("nome") or acao.get("alvo") or acao.get("url")
               or "").strip()[:60]
    onde = f", clicando em \"{alvo}\"" if alvo else ""
    if fala:
        return f"Vou {fala.rstrip('.').lower()}{onde}. Posso? E so dizer sim ou nao."

    porfamilia = {
        "apagar": "Vou APAGAR o que esta selecionado nesta tela",
        "pagar": "Vou seguir com um PAGAMENTO nesta tela",
        "comprar": "Vou CONFIRMAR uma compra nesta tela",
        "enviar": "Vou ENVIAR o que esta escrito nesta tela",
        "instalar": "Vou instalar ou desinstalar um programa",
        "formatar": "Vou FORMATAR um disco",
    }
    inicio = porfamilia.get(
        familia, "Vou fazer nesta tela uma coisa que nao tem volta")
    return f"{inicio}{onde}. Posso? E so dizer sim ou nao."


def _onde_e_este_pc() -> str:
    """Os caminhos REAIS desta maquina, para o modelo nao chutar nenhum."""
    import getpass  # noqa: PLC0415
    from pathlib import Path as _P  # noqa: PLC0415
    linhas = ["ESTA MAQUINA (use exatamente estes caminhos; NUNCA invente nome "
              "de usuario — ja aconteceu de voce procurar em "
              "C:\\Users\\Administrator numa maquina do Diego e nao achar nada):"]
    try:
        linhas.append(f"  usuario do Windows ... {getpass.getuser()}")
    except Exception:
        pass
    try:
        linhas.append(f"  pasta do usuario ..... {_P.home()}")
    except Exception:
        pass
    try:
        from actions.file_controller import (  # noqa: PLC0415
            _get_desktop, _get_downloads, _get_documents)
        linhas.append(f"  area de trabalho ..... {_get_desktop()}")
        linhas.append(f"  downloads ............ {_get_downloads()}")
        linhas.append(f"  documentos ........... {_get_documents()}")
    except Exception:
        pass
    for rotulo, chave in (("dados de programa (local)", "LOCALAPPDATA"),
                          ("dados de programa (roaming)", "APPDATA"),
                          ("programas 64 bits", "ProgramFiles"),
                          ("programas 32 bits", "ProgramFiles(x86)"),
                          ("dados comuns", "ProgramData"),
                          ("temporarios", "TEMP")):
        valor = os.environ.get(chave)
        if valor:
            linhas.append(f"  {rotulo:.<22} {valor}")
    linhas.append(
        "  PROCURE PROGRAMA NESSAS PASTAS. NAO varra o disco inteiro "
        "(`where /r C:\\`, `Get-ChildItem C:\\ -Recurse`): leva minutos, "
        "estoura o tempo do comando e volta vazio. Se nao souber onde o "
        "programa esta, leia o alvo do atalho dele na area de trabalho ou no "
        "menu Iniciar, ou rode `Get-Process <nome> | Select-Object Path` com "
        "ele aberto.")
    return "\n".join(linhas)


# A queixa e de programa que nao abre / deu erro? Muda o pedaco do manual.
#
# A PRIMEIRA VERSAO ERA ESTREITA DEMAIS (22/09/2026). O Diego disse:
#
#   "Eu to com um erro no aplicativo do Roblox Studio no meu computador, ta?
#    O erro ta bem no meio da tela. Corrija ele..."
#
# ...e isso NAO casou: a regra pedia "erro no meio da tela" exato, e ele disse
# "erro TA BEM no meio da tela"; e a outra alternativa exigia "erro" a menos de
# 30 letras de "aplicativo". Resultado: ele recebeu as receitas gerais
# (internet, impressora, Update) em vez do roteiro de programa que nao abre —
# o unico que ensina a LER a caixa de erro e procurar o arquivo renomeado.
#
# Agora e amplo de proposito: falar em ERRO junto de um programa ja basta. O
# preco de errar para mais e um pedaco de manual a toa; o de errar para menos
# foi um teste inteiro perdido.
_NAO_ABRE = re.compile(
    r"(nao (abre|abriu|inicia|iniciou|executa|roda|rodou|carrega|funciona|sobe)|"
    r"parou de (abrir|funcionar)|da erro|deu erro|dando (um )?erro|com erro|"
    r"esta com erro|ta dando|aparece (um )?erro|"
    r"erro (no|na|ao|quando|de|do|da|ta|esta|aparece|bem)|"
    r"(mensagem|caixa|tela) de erro|fecha sozinho|trava (ao|quando) abr|"
    r"nao entra|nao consigo (abrir|entrar|usar)|"
    r"(aplicativo|programa|app|software|jogo)\b.{0,60}\b(erro|problema|defeito|"
    r"travad|corromp|quebrad))", re.I)


# A TELA TAMBEM RESPONDE ESSA PERGUNTA, e melhor que a frase (22/09/2026).
# Uma caixa "Erro do sistema" aberta e prova de que o caso e de programa que
# nao abre, nao importa como o dono descreveu.
_JANELA_DE_ERRO = re.compile(
    r"(erro do sistema|system error|erro de aplica|application error|"
    r"\berro\b|\berror\b|nao pode ser iniciado|cannot start|"
    r"nao foi encontrad|was not found|missing)", re.I)


def _tela_mostra_erro(olhada: dict) -> bool:
    """Ha uma caixa de erro do Windows a vista?"""
    try:
        alvos = [str(olhada.get("titulo") or "")]
        alvos += [str(j) for j in (olhada.get("janelas") or [])]
        alvos += [str(e.get("nome") or "") for e in (olhada.get("elementos") or [])[:40]]
        return any(_JANELA_DE_ERRO.search(a) for a in alvos if a)
    except Exception:
        return False

# As secoes do manual, do jeito que ele foi escrito (uma linha em branco
# separa cada uma). A primeira palavra de cada secao e o que a identifica.
_SECAO_FERRAMENTA = "COMO SE CONSERTA WINDOWS"
_SECAO_METODO = "PRIMEIRO OLHE"
_SECAO_NAO_ABRE = "PROGRAMA QUE NAO ABRE"
_SECAO_RECEITAS = "RECEITAS QUE RESOLVEM"
_SECAO_PROIBIDO = "O QUE VOCE NAO FAZ"


def _secoes_do_manual() -> dict:
    """O manual quebrado nas suas secoes, pelo titulo de cada uma."""
    fora = {}
    atual = None
    for bloco in _MANUAL_DE_REPARO.split("\n\n"):
        for titulo in (_SECAO_FERRAMENTA, _SECAO_METODO, _SECAO_NAO_ABRE,
                       _SECAO_RECEITAS, _SECAO_PROIBIDO):
            if bloco.lstrip().startswith(titulo):
                atual = titulo
                break
        if atual:
            fora[atual] = (fora.get(atual, "") + "\n\n" + bloco).strip()
    return fora


def _fatiar(texto: str, teto: int = TETO_DA_LINHA_NA_VPS) -> list:
    """Parte um texto em pedacos que cabem no teto, cortando em linha inteira.

    Nunca no meio de uma frase: uma instrucao cortada ao meio e pior que
    instrucao nenhuma — foi o que aconteceu com o teto de 200 da VPS.
    """
    pedacos, atual = [], ""
    for linha in str(texto or "").split("\n"):
        # UMA LINHA SO, MAIOR QUE O TETO. Acontece de verdade: a saida de um
        # comando as vezes vem inteira numa linha. Sem este corte por
        # caractere, o pedaco saia com 5.000 letras e a VPS o cortava — de
        # volta ao defeito que este fatiador existe para impedir.
        while len(linha) > teto:
            if atual:
                pedacos.append(atual.rstrip())
                atual = ""
            pedacos.append(linha[:teto])
            linha = linha[teto:]
        if len(atual) + len(linha) + 1 > teto and atual:
            pedacos.append(atual.rstrip())
            atual = ""
        atual += linha + "\n"
    if atual.strip():
        pedacos.append(atual.rstrip())
    return pedacos


def _manual_para(tarefa: str) -> list:
    """So os pedacos do manual que servem para ESTA tarefa.

    Mandar o manual inteiro em todo passo custa tempo — medido em 22/09/2026:
    o passo subiu de ~5,5 s para ~7,6 s quando o pedido engordou, e o Diego
    sentiu na hora ("antes ele mexia mais rapido"). Entao a queixa escolhe: a
    ferramenta e o metodo vao sempre; depois vai OU o roteiro de "nao abre" OU
    as receitas gerais, nunca os dois.
    """
    secoes = _secoes_do_manual()
    escolhidas = [secoes.get(_SECAO_FERRAMENTA, ""), secoes.get(_SECAO_METODO, "")]
    if _NAO_ABRE.search(tarefa or ""):
        escolhidas.append(secoes.get(_SECAO_NAO_ABRE, ""))
    else:
        escolhidas.append(secoes.get(_SECAO_RECEITAS, ""))
    escolhidas.append(secoes.get(_SECAO_PROIBIDO, ""))
    saida = []
    for texto in escolhidas:
        if texto.strip():
            saida.extend(_fatiar(texto))
    return saida


# "desative TODAS as notificacoes" — quando o pedido fala em tudo, conferir a
# primeira tela nao basta: e preciso ter rolado a pagina inteira.
# "nenhum" e "qualquer" SAIRAM em 23/09/2026: "nao mexa em NENHUMA outra aba"
# e restricao, nao pedido de percorrer tudo — e casava aqui, cobrava uma
# rolagem impossivel e a tarefa certa terminava como "a tela nao reagiu".
_TAREFA_DIZ_TUDO = re.compile(
    r"\b(tod[oa]s?|tudo|geral|completa(mente)?|inteir[oa]|cada um)\b", re.I)

# ── AS REGRAS SUMIAM NO PASSO 6 (achado no teste de 22/09/2026) ────────────
#
# O pedido para a VPS levava `historico[-12:]` — as doze ultimas anotacoes. E
# as REGRAS DA TAREFA e o MANUAL DE REPARO sao as PRIMEIRAS entradas do
# historico. Cada passo acrescenta uma ou duas linhas; do passo seis em diante
# as regras saiam da janela e o Codex nunca mais as via.
#
# Isso explica o teste do Roblox Studio: o manual foi enviado (o log diz
# "mandei o manual de reparo"), o Codex leu nos primeiros passos, e a partir do
# sexto estava sozinho de novo — sem saber que podia rodar comando, sem a regra
# de nao clicar duas vezes, sem os enderecos do Windows. Resultado: 60 passos
# clicando em "programas instalados" e no Roblox Player, sem nunca ler o erro
# que estava escrito na tela.
#
# Agora o que e REGRA vai FIXO em todo pedido, e so o relato dos passos rola.

# Quanto de uma anotacao VELHA continua valendo. A saida de um comando de tres
# passos atras nao precisa vir inteira: o que importa e que ele rodou e como
# terminou. So as `INTEIRAS_NO_FIM` ultimas vao completas.
_RESUMO_DA_VELHA = 260
INTEIRAS_NO_FIM = 3


def _historico_para_a_vps(historico: list, fixas: int, ultimas: int = 12,
                          inteiras: int = INTEIRAS_NO_FIM) -> list:
    """As regras (sempre) + as ultimas anotacoes (rolando, as velhas em resumo).

    O TAMANHO E TEMPO (22/09/2026). Depois que a VPS passou a aceitar 1.800
    caracteres por linha, o pedido de cada passo engordou e o Diego sentiu na
    hora: "esta muito lento, antes ele mexia mais rapido" — de ~5,5 s para
    ~7,6 s por passo. Doze saidas de comando inteiras sao ~18 mil caracteres
    em toda volta, e o Codex le tudo isso de novo a cada passo.

    O corte certo nao e mandar menos passos: e mandar os velhos RESUMIDOS. O
    que ele precisa saber de um comando de cinco passos atras e que ele rodou e
    no que deu — nao a listagem inteira.
    """
    cabeca = historico[:fixas]
    corpo = historico[fixas:][-ultimas:]
    if len(corpo) > inteiras:
        velhas, novas = corpo[:-inteiras], corpo[-inteiras:]
        corpo = [(v if len(v) <= _RESUMO_DA_VELHA
                  else v[:_RESUMO_DA_VELHA] + " [...]") for v in velhas] + novas
    return cabeca + corpo


# A VPS respondeu, mas a resposta nao virou movimento nenhum. Nao e ponte
# caida: quase sempre e JSON quebrado por comando comprido demais.
_RESPOSTA_SEM_MOVIMENTO = re.compile(
    r"(nao respondeu um movimento valido|sem movimento|movimento invalido|"
    r"SEM JSON|json invalido|resposta vazia)", re.I)


# Como a VPS avisa que a contagem de passos DELA acabou. Nao e falha de rede
# nem do modelo: e so o job ficando velho. Ver o tratamento no laco.
_E_TETO_DA_VPS = re.compile(
    r"(limite de passos|limite de etapas|passos? excedid|step limit|"
    r"max steps|too many steps|job (expirado|encerrado|finalizado))", re.I)


# Quando a tarefa e de CONSERTO. Se casar, o manual acima vai junto.
_TAREFA_DE_CONSERTO = re.compile(
    r"\b(conserta|consertar|corrig|repara|reparar|arruma|arrumar|resolve|"
    r"resolver|nao funciona|nao abre|nao liga|nao conecta|nao imprime|"
    r"parou de funcionar|travad|travando|lento|lentid|erro|bug|problema|"
    r"defeito|corromp|quebrad|falha|tela azul|reinicia sozinho|diagnostic|"
    r"virus|limpa o pc|limpar o pc|otimiz|atualizar o windows|update|"
    r"sem internet|sem som|sem audio|nao tem som|driver)\b", re.I)


# ── OS INTERRUPTORES DA TELA, LIDOS PELO NOME DA LINHA ───────────────────
#
# Pedido do Diego em 07/09/2026, depois de mandar desativar as notificacoes:
#
#   "toda entrada que ele for fazer, o Codex tem que ver a imagem que recebeu e
#    ver que na proxima imagem nao tem nada que ele mexeu, nada que ele
#    desativou que nao seja parte daquilo que pediu... tem que ter uma
#    autocorrecao tambem"
#
# Na tela dele o JARVIS desligou as notificacoes (certo) e de quebra LIGOU o
# "Nao incomodar", que ninguem pediu. Ele nunca soube disso porque nada compara
# a tela de antes com a de depois.
#
# Aqui essa comparacao vira texto. O Windows escreve o estado numa etiqueta
# separada ("Ativado"/"Desativado") ao lado do nome da linha — entao eu junto os
# dois pela ALTURA: a etiqueta de estado e o rotulo mais a esquerda, na mesma
# linha. Sai um dicionario {"Nao incomodar": "Ativado"}, e a diferenca entre
# dois passos e exatamente o que ele acabou de mexer.
_PALAVRAS_DE_ESTADO = ("ativado", "desativado", "ativada", "desativada",
                       "ligado", "desligado", "ligada", "desligada",
                       "on", "off", "habilitado", "desabilitado")


def _estados_da_tela(elementos: list) -> dict:
    """{rotulo da linha: estado} para tudo que tem interruptor visivel."""
    estados = {}
    for el in elementos or []:
        nome = (el.get("nome") or "").strip()
        if _sem_acento_simples(nome.lower()) not in _PALAVRAS_DE_ESTADO:
            continue
        y, x = el.get("y", 0), el.get("x", 0)
        vizinhos = [o for o in elementos
                    if o is not el and abs(o.get("y", 0) - y) <= 22
                    and o.get("x", 0) < x and (o.get("nome") or "").strip()
                    and _sem_acento_simples(o["nome"].strip().lower())
                    not in _PALAVRAS_DE_ESTADO]
        if not vizinhos:
            continue
        rotulo = min(vizinhos, key=lambda o: o.get("x", 0))["nome"].strip()[:48]
        estados[rotulo] = nome
    return estados


def _virou_o_que(antes: dict, depois: dict, limite: int = 6) -> list:
    """O que trocou de estado de um passo para o outro."""
    mudancas = []
    for rotulo, agora in (depois or {}).items():
        antigo = (antes or {}).get(rotulo)
        if antigo and antigo != agora:
            mudancas.append(f'"{rotulo}" passou de {antigo} para {agora}')
    return mudancas[:limite]


# ── A TRAVA QUE IMPEDE DESFAZER O QUE JA ESTAVA CERTO (22/09/2026) ──────────
#
# Queixa do Diego, de novo, agora na v1.0.27: "eu peco uma coisa simples, tipo
# desabilitar as notificacoes. Ele vai, ate abre certo, DESABILITA, ai depois
# HABILITA NOVAMENTE. Nem sempre da esse erro, algumas vezes so."
#
# Em 07/09 isto ja tinha sido atacado do lado do PEDIDO: a conferencia virou
# so-leitura, entao ele nao pode clicar enquanto confere. O que sobrou — e o
# que ainda acontece "algumas vezes" — e o clique de FORA da conferencia: o
# Codex perde o fio, volta numa tela em que ja mexeu e clica de novo no mesmo
# interruptor. Interruptor clicado duas vezes volta ao que era.
#
# Instrucao nao resolve isso, porque nao e desobediencia: e ele nao lembrar.
# Quem resolve e o PROGRAMA lembrar por ele. Toda vez que um item muda de
# estado, fica anotado em `mexidos`. Antes de executar um clique, se o alvo for
# um item ja mexido E ele ja estiver no estado novo, o movimento e BLOQUEADO e
# volta um recado dizendo o que ja foi feito.
#
# So bloqueia o que DESFAZ. Se o dono mandou corrigir, o estado atual e o
# antigo e o clique passa.

# A unica saida da trava: quando o proprio movimento diz que o DONO mandou
# desfazer/religar. Sem isto, uma correcao pedida por ele ficaria travada.
_PEDE_DESFAZER = re.compile(
    r"\b(desfaz|desfazer|desfac|reverter|revertendo|volta(r|ndo)?\s+(ao|para|pro)|"
    r"religa|religar|reativa|reativar|ativar de novo|ligar de novo|"
    r"o dono (mandou|pediu|quer)|como (ele|o dono) pediu)\b", re.I)


def _chave_do_item(nome: str) -> str:
    return re.sub(r"\s+", " ", str(nome or "").strip().lower())[:48]


def _alvo_do_clique(acao: dict) -> str:
    """O nome do item que este movimento vai apertar (vazio se for por pixel)."""
    tipo = str(acao.get("acao") or "").lower().strip()
    if tipo not in ("clicar", "click", "clicar_nome", "clicar_texto", "acionar",
                    "apertar", "invocar", "clicar_duplo", "double_click"):
        return ""
    for campo in ("nome", "texto", "alvo", "elemento_nome"):
        valor = acao.get(campo)
        if isinstance(valor, str) and valor.strip():
            return _chave_do_item(valor)
    return ""


def _desfaria(acao: dict, mexidos: dict, estados_agora: dict) -> tuple:
    """Este movimento desfaz algo que ja foi feito? Devolve (sim, recado)."""
    alvo = _alvo_do_clique(acao)
    if not alvo or alvo not in mexidos:
        return False, ""
    antes, depois, passo_de_antes = mexidos[alvo]
    # Como esta AGORA na tela. Se ja voltou sozinho para o estado antigo, o
    # clique nao desfaz nada — deixa passar.
    atual = None
    for rotulo, estado in (estados_agora or {}).items():
        if _chave_do_item(rotulo) == alvo:
            atual = estado
            break
    if atual is not None and atual != depois:
        return False, ""
    return True, (
        f'BLOQUEEI este movimento. Voce JA mexeu em "{alvo}" no passo '
        f'{passo_de_antes}: ele passou de {antes} para {depois}, e continua '
        f'{depois} na tela. Clicar de novo VOLTA para {antes} — foi exatamente '
        f'assim que a tarefa passada foi desfeita depois de estar certa. '
        f'Este item ESTA PRONTO: siga para o que ainda falta, ou responda '
        f'"concluido" se nao falta nada. Se o dono pediu para DESFAZER este '
        f'item, diga isso na fala do movimento que eu libero.')


# ── NA CONFERENCIA ELE SO OLHA (07/09/2026, e custou uma tarefa inteira) ──
#
# A primeira versao da conferencia dizia "se faltou alguma coisa, faca o que
# falta agora". Deu ruim de um jeito que eu devia ter previsto: o Codex, sem
# certeza do que ja tinha feito, CLICAVA DE NOVO nos mesmos interruptores — e
# interruptor clicado duas vezes volta ao que era. O Diego descreveu o
# resultado melhor do que eu:
#
#   "ele desativou, ai tava tudo certo, ai foi e ATIVOU novamente; ai voltou
#    pro sistema, ai voltou pro Defender, ai depois ativou tudo de novo"
#
# Foram 42 passos e 455 segundos ligando e desligando a mesma coisa.
#
# A conferencia agora e SO LEITURA: ele pode rolar, focar janela e esperar para
# enxergar a pagina inteira, e tem de RESPONDER o que viu. Clicar, digitar e
# teclar ficam bloqueados naquele passo — se ele tentar, leva o recado de volta
# e nada acontece na tela. So DEPOIS de ele dizer o que falta e que o conserto
# e liberado. Olhar e agir viraram dois momentos, e nao um.
_ACOES_DE_OLHAR = {
    "rolar", "scroll", "rolar_ate", "focar_janela", "esperar", "aguardar",
    "concluido", "done", "pronto", "finalizado", "falhou", "fail", "erro",
    "responder", "explicar", "avisar", "perguntar", "ask",
    # Comando de LEITURA tambem e olhar: na conferencia de um conserto, ver o
    # servico de pe (`Get-Service`) prova mais que qualquer foto de tela. O
    # filtro do que e leitura esta no `_comando_so_le`.
    "comando",
    # Fechar caixa de erro tambem vale na conferencia: elas tapam a tela que
    # ele precisa enxergar para conferir.
    "fechar_erros", "fechar_erro", "fechar_avisos", "limpar_erros",
    # Ler o texto e chegar perto tambem sao olhar (23/09/2026): conferir um
    # valor pela foto reduzida e o que fazia ele confirmar coisa errada.
    "ler_tela", "ler", "ler_texto", "ler_pagina", "ampliar", "zoom", "aproximar",
}

# Movimentos que NAO mexem na tela: a tela nao mudar depois deles e o
# esperado, e nao conta como "a tela parou de reagir".
_NAO_MEXEM_NA_TELA = {
    "comando", "powershell", "cmd", "executar_comando", "rodar",
    "esperar", "wait", "aguardar", "responder", "explicar", "avisar",
    "ler_tela", "ler", "ler_texto", "ler_pagina", "ampliar", "zoom", "aproximar",
    "pesquisar", "pesquisar_web", "buscar_na_internet",
}

# ── O QUE E COMANDO DE LEITURA (22/09/2026) ────────────────────────────────
#
# A PRIMEIRA VERSAO PERGUNTAVA "COMECA COM VERBO DE LEITURA?", e isso reprovou
# o comando certo no teste: o Codex mandou
#
#     $p='...'; [pscustomobject]@{Qt5Widgets=(Test-Path (Join-Path $p ...))}
#
# ...que so LE, mas comeca com `$p=`. A conferencia bloqueou ("conferencia:
# bloqueei comando") e ele nao conseguiu provar que tinha consertado.
#
# A pergunta certa e a INVERSA: o comando tem algum verbo que MUDA alguma
# coisa? Se nao tem, e leitura, comece como comecar. Lista de escrita e curta
# e fechada; tudo que nao esta nela passa.
_VERBO_QUE_MUDA = re.compile(
    r"(\b(remove|set|stop|start|restart|clear|disable|enable|new|add|reset|"
    r"repair|rename|move|copy|out-file|write|install|uninstall|register|"
    r"unregister|suspend|resume|invoke|push|pop|expand|compress|mount|"
    r"dismount|initialize|optimize|update|save|export|import)-[a-z]|"
    r"\b(del|erase|rd|rmdir|md|mkdir|ren|move|copy|xcopy|robocopy|attrib|"
    r"takeown|icacls|reg\s+(add|delete|import)|sc\s+(config|delete|start|stop)|"
    r"net\s+(start|stop|user|localgroup)|taskkill|shutdown|bcdedit|"
    r"sfc\s+/scannow|chkdsk\s+[a-z]:\s*/[fr]|wsreset|cleanmgr)\b|"
    r"\|\s*(stop|remove|set|start|restart|clear|disable|enable|new|add|reset|"
    r"repair|rename|move|copy|out-file)-|"
    r"[^>]>[^>]|>>)", re.I)


def _comando_so_le(acao: dict) -> bool:
    if str(acao.get("acao") or "").lower().strip() not in (
            "comando", "powershell", "cmd", "executar_comando", "rodar"):
        return True          # nao e comando: quem decide e a lista de cima
    texto = str(acao.get("texto") or acao.get("comando") or acao.get("cmd") or "")
    return not bool(_VERBO_QUE_MUDA.search(texto))


def _parece_conclusao_boa(texto: str) -> bool:
    """Ele terminou dizendo que FEZ, ou dizendo que NAO CONSEGUIU?

    So vale conferir o que ele afirma ter feito. Desistencia ("nao consegui",
    "nao encontrei") ja e a resposta honesta e nao ha o que checar na tela.
    """
    t = _sem_acento_simples(str(texto or "").lower())
    ruins = ("nao consegui", "nao foi possivel", "nao encontrei", "nao achei",
             "nao deu", "falhei", "desisti", "nao existe", "sem permissao",
             "precisa de administrador", "nao consigo")
    return not any(r in t for r in ruins)


def _acoes_pedidas(resposta: dict) -> list[dict]:
    """Tudo o que a VPS mandou, antes do corte: `acoes` (lista) ou `acao` (uma so)."""
    if isinstance(resposta.get("acoes"), list):
        return [a for a in resposta["acoes"] if isinstance(a, dict) and a.get("acao")]
    if resposta.get("acao"):
        return [resposta]
    return []


def _um_movimento_por_vez(acoes: list[dict]) -> list[dict]:
    """So o PRIMEIRO movimento; o resto espera a foto nova. Ver `MAX_ACOES_POR_PASSO`.

    Excecao unica: "digitar" seguido de Enter ou Tab vai junto — confirmar o
    texto que acabou de ser escrito nao depende do que aparece na tela.
    """
    if not acoes:
        return []
    saida = list(acoes[:MAX_ACOES_POR_PASSO])
    tipo = str(saida[-1].get("acao") or "").lower().strip()
    if tipo in ("digitar", "escrever", "type") and len(acoes) > len(saida):
        seguinte = acoes[len(saida)]
        tipo2 = str(seguinte.get("acao") or "").lower().strip()
        teclas = str(seguinte.get("teclas") or seguinte.get("tecla") or "").lower().strip()
        if tipo2 in ("tecla", "key") and teclas in ("enter", "return", "tab"):
            saida.append(seguinte)
    return saida


def _lista_de_acoes(resposta: dict) -> list[dict]:
    """Os movimentos DESTE passo: um por vez (ver `_um_movimento_por_vez`)."""
    return _um_movimento_por_vez(_acoes_pedidas(resposta))


def _so_clique_cego(assinaturas: list) -> bool:
    """Os ultimos movimentos foram todos clique em coordenada, sem nome?

    Clique em x,y e legitimo (video, imagem, area de desenho), mas quando ele
    se repete e nada muda o caminho e outro: pedir o elemento pelo NOME, que o
    Windows sabe onde fica. E o conselho que faltava no historico.
    """
    ultimos = [a for a in (assinaturas or [])[-3:] if a]
    if len(ultimos) < 2:
        return False
    for a in ultimos:
        partes = a.split("|")
        # ("acao", "elemento", "nome", "x", ...) — ver `_assinatura_do_movimento`
        if len(partes) < 5:
            return False
        acao, elemento, nome, x = partes[0], partes[1], partes[2], partes[3]
        if not acao.startswith("clic") or elemento or nome or not x:
            return False
    return True


def _assinatura_do_movimento(acao: dict) -> str:
    # `nome`, `caminho` e a 2a ponta entram aqui desde 04/09/2026: sem eles,
    # "arrasta A para B" e "arrasta A para C" tinham a MESMA assinatura, e o
    # anti-teimosia acusava repeticao em dois movimentos diferentes.
    return "|".join(str(acao.get(k) or "") for k in
                    ("acao", "elemento", "nome", "x", "y", "texto", "teclas", "url",
                     "titulo", "caminho", "elemento2", "nome2", "x2", "y2",
                     "direcao", "modificador"))


def _sair_da_frente(log) -> None:
    """Minimiza a janela do proprio Bob, se for ela que esta na frente.

    O dono fala COM o Bob, entao na hora do pedido e a janela do Bob que esta em
    primeiro plano. Se ela ficar ali, a primeira foto e a lista de elementos sao
    do proprio Bob — e o Codex gasta passos (ou pior, clica no HUD) so para
    chegar onde o servico e. Minimizar e o que uma pessoa faria.

    So minimiza se a janela for DESTE processo: janela de outro programa nunca.
    """
    try:
        import os  # noqa: PLC0415
        import win32con  # noqa: PLC0415
        import win32gui  # noqa: PLC0415
        import win32process  # noqa: PLC0415
        h = win32gui.GetForegroundWindow()
        if not h:
            return
        _tid, pid = win32process.GetWindowThreadProcessId(h)
        if pid != os.getpid():
            return
        win32gui.ShowWindow(h, win32con.SW_MINIMIZE)
        time.sleep(0.35)
        log("[usar_computador] minimizei a janela do JARVIS para liberar a tela")
    except Exception:
        pass


def _manter_fora_da_frente(log, avisado: dict) -> None:
    """A janela do JARVIS voltou a aparecer no meio da tarefa? Minimiza de novo.

    Pedido do Diego em 07/09/2026: *"quando eu falo pra ele ativar o
    computador, ele minimiza o JARVIS, porem depois volta ele pra tela; o certo
    e minimizar e so voltar depois que finalizar"*.

    O `_sair_da_frente` so age UMA vez, no comeco, e so se a janela estiver em
    primeiro plano. Durante a tarefa ela reaparece (a interface se levanta
    sozinha quando fala, quando o microfone reabre, quando um aviso sobe) e ai
    ela entra na foto — e o Codex passa a ver o HUD em vez do servico.

    CUIDADO QUE JA CUSTOU UM ERRO: eu tinha filtrado "so janela COM titulo",
    achando que isso deixava a faixa "JARVIS ESTA USANDO O COMPUTADOR" de fora.
    Nao deixava — e o Diego viu na hora: *"o nome la de cima, que ta la aperte
    ESC para parar, ele some e vai la pra baixo, no canto esquerdo"*. Era a
    faixa MINIMIZADA, que o Windows encolhe naquele tocinho do canto quando a
    janela nao tem botao na barra de tarefas.
    O que separa as duas de verdade e o estilo WS_EX_TOOLWINDOW: a faixa e uma
    janela de ferramenta (`Qt.WindowType.Tool`), a janela principal nao. Filtrar
    por isso e exato; filtrar por titulo era palpite.
    """
    try:
        import os  # noqa: PLC0415
        import ctypes  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415
        user32 = ctypes.windll.user32
        meu_pid = os.getpid()
        alvos = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        def _visitar(h, _lp):
            try:
                if not user32.IsWindowVisible(h) or user32.IsIconic(h):
                    return True
                # GWL_EXSTYLE = -20, WS_EX_TOOLWINDOW = 0x00000080
                if user32.GetWindowLongW(h, -20) & 0x00000080:
                    return True            # e a faixa (janela de ferramenta): fica
                if user32.GetWindowTextLengthW(h) <= 0:
                    return True
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
                if pid.value == meu_pid:
                    alvos.append(h)
            except Exception:
                pass
            return True

        user32.EnumWindows(_visitar, 0)
        for h in alvos:
            user32.ShowWindow(h, 6)        # SW_MINIMIZE
        if alvos and not avisado.get("v"):
            avisado["v"] = True
            log("[usar_computador] a janela do JARVIS reapareceu; minimizei de novo")
    except Exception:
        pass


# ── O FECHO: avisar que o computador voltou a ser dele ─────────────────────
#
# Pedido do cliente em 03/09/2026, depois do primeiro teste que deu certo: o
# Bob dizia so o resultado ("manual de motores aberto") e a pessoa ficava sem
# saber se ainda era para nao encostar no mouse. Como ele avisa no comeco que
# vai assumir o computador, tem de avisar no fim que devolveu — em QUALQUER
# tarefa, tenha dado certo, errado ou tenha sido interrompida.
_FECHOS = (
    "Seu computador esta liberado.",
    "Pode usar o computador de novo.",
    "Ja pode mexer no mouse e no teclado.",
    "O computador e seu de novo.",
)


def _com_fecho(texto: str) -> str:
    texto = (texto or "Terminei.").strip()
    if not texto.endswith((".", "!", "?")):
        texto += "."
    return f"{texto} {random.choice(_FECHOS)}"


# A VOZ PRIMEIRO, A TELA DEPOIS (22/09/2026, noite).
#
# Queixa do Diego: o aviso "nao mexa no mouse nem no teclado" saia picotado,
# "falando, cortando, toda hora". Uma das causas era esta: o laco ja comecava a
# trabalhar ENQUANTO a voz tocava — foto da tela, varredura dos elementos,
# pedido a VPS —, tudo no mesmo processo que toca o som. O registro mostra
# `fim_de_fala_falso` em rajada por 40 s depois do aviso: a caixa ficava sem o
# que tocar, parava, voltava.
#
# Agora cada passo espera a fala acabar antes de mexer em qualquer coisa. Custa
# o tempo da propria fala (o aviso uma vez, um lembrete a cada 2 min) e o dono
# ouve a frase inteira. O teto existe para uma marca de "falando" presa nunca
# travar a tarefa.
_TETO_ESPERA_DA_VOZ_S = 30.0


def _esperar_a_voz(falando, parar=None, teto_s: float = _TETO_ESPERA_DA_VOZ_S) -> float:
    """Segura o laco enquanto o JARVIS fala. Devolve quantos segundos esperou."""
    if not falando:
        return 0.0
    t0 = time.monotonic()
    while time.monotonic() - t0 < teto_s:
        try:
            if not falando():
                break
        except Exception:
            break
        try:
            if parar and parar():
                break
        except Exception:
            pass
        time.sleep(0.1)
    return time.monotonic() - t0


def usar_computador(parameters: dict | None = None, player=None, falar=None,
                    perguntar=None, deve_parar=None, narrar: bool = True,
                    correcoes=None, falar_aviso=None, falando=None,
                    ao_esc=None) -> str:
    """Entrada da ferramenta: roda a tarefa e devolve o resultado JA COM o fecho.

    O fecho so entra quando o Bob chegou a assumir o computador. Recusa de
    entrada ("faltou dizer o que fazer", "a ponte esta offline") nao mexeu em
    nada na tela, entao dizer "pode usar o computador de novo" ali seria
    confuso.
    """
    try:  # Linux no Wayland: mouse e teclado bloqueados (core/plataforma.py)
        from core.plataforma import bloqueio_do_mouse  # noqa: PLC0415
        recado = bloqueio_do_mouse()
        if recado:
            return recado
    except Exception:
        pass
    p = parameters or {}
    tem_tarefa = bool(str(p.get("tarefa") or p.get("task") or "").strip())
    relay = getattr(player, "_codex_relay", None) if player is not None else None
    pronto = bool(relay is not None and hasattr(relay, "pedir_passo_na_tela"))
    # UMA POR VEZ (ver `_TRAVA_DO_LACO`): a nova pede para a anterior parar e
    # espera ela soltar o mouse. A ultima ordem do dono e a que vale.
    anterior = _LACO_ATUAL.get("parar")
    if anterior is not None:
        anterior.set()
    if not _TRAVA_DO_LACO.acquire(timeout=25):
        return ("Ainda estou terminando o que eu estava fazendo na tela. "
                "Me pede de novo em alguns segundos.")
    meu = threading.Event()
    _LACO_ATUAL["parar"] = meu
    try:
        resultado = _usar_computador(parameters, player, falar, perguntar, deve_parar,
                                     narrar, correcoes, falar_aviso, falando, trocado=meu,
                                     ao_esc=ao_esc)
    finally:
        if _LACO_ATUAL.get("parar") is meu:
            _LACO_ATUAL["parar"] = None
        _TRAVA_DO_LACO.release()
    return _com_fecho(resultado) if (tem_tarefa and pronto) else resultado


def _usar_computador(parameters: dict | None = None, player=None, falar=None,
                     perguntar=None, deve_parar=None, narrar: bool = True,
                     correcoes=None, falar_aviso=None, falando=None,
                     trocado: threading.Event | None = None, ao_esc=None) -> str:
    """O laco de verdade. Ver `usar_computador`, que e a porta de entrada.

    `falar(texto)`      narra ao dono (curto).
    `perguntar(texto)`  pergunta em voz e devolve a resposta (None se calou).
    `deve_parar()`      True quando o dono mandou parar.
    `narrar`            liga a voz de espera (desliga em teste automatico).
    `falando()`         True enquanto ainda sai voz pela caixa. O laco espera
                        a fala acabar antes de cada passo (ver `_esperar_a_voz`).
    """
    p = parameters or {}
    tarefa = str(p.get("tarefa") or p.get("task") or "").strip()
    if not tarefa:
        return "Faltou dizer o que fazer na tela."
    relay = getattr(player, "_codex_relay", None) if player is not None else None
    if relay is None or not hasattr(relay, "pedir_passo_na_tela"):
        return "A ponte com o servidor ainda nao esta pronta. Tente de novo em instantes."

    def log(msg: str) -> None:
        if player is not None and hasattr(player, "write_log"):
            try:
                player.write_log(msg)
            except Exception:
                pass

    # Aviso na tela ("BOB ESTA USANDO O COMPUTADOR"), se a interface tiver.
    def aviso(ligado: bool, texto: str = "") -> None:
        if player is None:
            return
        for nome in ("mostrar_aviso_pc", "aviso_computador"):
            fn = getattr(player, nome, None)
            if callable(fn):
                try:
                    fn(ligado, texto)
                except Exception:
                    pass
                return

    pergunta_pendente = {"v": False}

    def perguntar_seguro(texto: str, espera_s: float = 0.0, so_sim_ou_nao: bool = False):
        if not perguntar:
            return None
        pergunta_pendente["v"] = True
        try:
            # 40 s, e nao os 25 de antes, quando quem chama pede: a pergunta
            # LEVA de 3 a 5 s para ser falada, o microfone so reabre depois
            # disso, e ai o dono ainda precisa pensar e responder.
            #
            # `so_sim_ou_nao` avisa o main de que a resposta esperada e curta:
            # com isso ele afrouxa o portao de volume (a resposta do dono vinha
            # sendo descartada como "som de longe") e, em troca, so aceita como
            # resposta o texto que PARECA sim ou nao — a televisao continua de
            # fora. As duas metades tem de andar juntas.
            if espera_s or so_sim_ou_nao:
                try:
                    return perguntar(texto, espera_s or 25.0, so_sim_ou_nao)
                except TypeError:
                    return perguntar(texto)
            return perguntar(texto)
        finally:
            pergunta_pendente["v"] = False

    narrador = None
    if narrar and falar:
        narrador = Narrador(falar, pausado=lambda: pergunta_pendente["v"],
                            falar_aviso=falar_aviso)

    job_id = secrets.token_hex(12)
    historico: list[str] = []
    t0 = time.monotonic()
    assinatura_anterior = None
    impressao_anterior = ""
    foco_anterior = ""
    sem_mudar = 0
    ultimos_movimentos: list[str] = []
    avisou_mouse_em = 0.0
    avisou_vivo_em = 0.0
    erros_seguidos = 0
    trocas_de_job = 0          # quantas vezes a contagem da VPS recomecou
    # O passo anterior so rodou comando? Entao a tela nao ter mudado e o
    # esperado, e nao sinal de que ele esta clicando no vazio.
    so_comando_no_ultimo_passo = False
    # Ele mexeu em registro/politica nesta tarefa? Ver a regra G: mexer ali nao
    # e a tarefa terminada, porque o dono confere pelo interruptor da tela.
    mexeu_no_registro = False
    # Comandos de LEITURA seguidos sem consertar nada. Ver o teto no laco.
    buscas_seguidas = 0
    investigado = {"v": False}
    correcoes_ditas: list[str] = []
    # O QUE ELE JA MEXEU NESTA TAREFA, para nunca desfazer sozinho.
    # nome do item -> (estado de antes, estado de agora, em que passo).
    mexidos: dict = {}
    # O que o dono JA autorizou nesta tarefa (por familia: apagar, pagar...).
    #
    # O PEDIDO DELE JA E A AUTORIZACAO (04/09/2026). Quando ele diz "apague os
    # arquivos temporarios", perguntar "posso apagar?" nao protege ninguem — ele
    # acabou de mandar. Perguntar assim mesmo foi o que matou a tarefa no teste:
    # a segunda pergunta ficou sem resposta e tudo parou. Entao a familia que
    # aparece NO PEDIDO ja entra autorizada; o que ele NAO pediu (o modelo
    # decidir desinstalar, comprar, formatar) continua passando pela pergunta.
    autorizados: set[str] = set()
    _familia_pedida = _familia_do_risco(tarefa, {})
    if _familia_pedida not in ("outro", "apagar_de_vez"):
        autorizados.add(_familia_pedida)
        log(f"[usar_computador] '{_familia_pedida}' ja veio autorizado no proprio pedido")
    # O FREIO DE ESC. Vale para tudo daqui em diante: `parar_agora` e o unico
    # lugar que o laco pergunta, e ele soma as duas portas — a voz ("para") e a
    # tecla. Quem apertou fica registrado, porque o aviso falado no fim e
    # diferente ("voce apertou ESC" x "como voce pediu").
    freio_esc = FreioEsc(ao_apertar=ao_esc)
    freio_esc.ligar()

    def parar_agora() -> bool:
        if freio_esc.apertado:
            return True
        if trocado is not None and trocado.is_set():
            return True
        try:
            return bool(deve_parar and deve_parar())
        except Exception:
            return False

    ctx = {"mouse_esperado": None, "escala_x": 1.0, "escala_y": 1.0, "por_numero": {},
           "desloc_x": 0, "desloc_y": 0,
           "deve_parar": parar_agora, "freio_esc": freio_esc}

    def _parei() -> str:
        if freio_esc.apertado:
            return "Parei na hora, voce apertou ESC."
        if trocado is not None and trocado.is_set():
            return "Deixei essa de lado para fazer o seu pedido novo."
        return "Parei, como voce pediu."

    # O QUE O BOB JA SABE (ver `receitas_pc.py`): se o pedido e de um site
    # conhecido, os passos obvios saem de cor — sem foto, sem VPS, em 2 s — e o
    # que o Bob sabe daquele sistema (rotas, nomes dos botoes) segue junto com a
    # tarefa, para o Codex nao ficar cacando menu na tela.
    dicas = ""
    passos_receita, nome_receita, depois_da_receita = None, "", "visao"

    def _tarefa_completa(base_dicas: str = "") -> str:
        """A tarefa como a VPS a recebe: o pedido + as dicas + ESTA maquina."""
        return "\n\n".join(x for x in (tarefa, base_dicas, _onde_e_este_pc()) if x)

    # Ja nasce com os caminhos: se a escolha de receita falhar, eles continuam
    # indo. Era o que faltava quando ele inventou o nome do usuario.
    tarefa_vps = _tarefa_completa()

    def _escolher_receita() -> None:
        # SO DEPOIS DE MINIMIZAR O BOB. A janela em primeiro plano e parte da
        # pergunta ("qual sistema ele esta usando?"), e antes de minimizar a
        # resposta e sempre "o Bob" — o que fazia o pedido "abre a injecao
        # dessa placa" nao reconhecer o Simplo que estava aberto atras.
        nonlocal dicas, passos_receita, nome_receita, depois_da_receita, tarefa_vps
        try:
            from actions import receitas_pc  # noqa: PLC0415
            titulo_agora = tela_visao.janela_em_frente()[1]
            dicas = receitas_pc.dicas_para_o_codex(tarefa, titulo_agora)
            passos_receita, nome_receita, depois_da_receita = receitas_pc.receita_para(
                tarefa, titulo_agora)
            # OS CAMINHOS DESTA MAQUINA VAO NA TAREFA, NAO SO NO HISTORICO
            # (22/09/2026). Eles JA iam no historico, e mesmo assim ele
            # escreveu `C:\Users\D\AppData` e `C:\Users\Administrator` — o
            # historico e "o que ja foi feito", e o modelo trata como relato,
            # nao como regra. Na TAREFA, que e o pedido em si, ele obedece.
            tarefa_vps = _tarefa_completa(dicas)
        except Exception as exc:
            log(f"[usar_computador] receitas fora: {str(exc)[:100]}")

    try:
        # "aperte ESC para parar" saiu porque na tela lia-se "para para"
        # (04/09/2026, o Diego viu na faixa). "ESC interrompe" e mais curto e
        # nao repete palavra.
        aviso(True, "JARVIS ESTA USANDO O COMPUTADOR  ·  ESC interrompe  ·  "
                    "nao mexa no mouse nem no teclado")
        _sair_da_frente(log)          # a janela do Bob nao pode tapar o servico
        tela_visao.aquecer()          # acorda a acessibilidade do navegador
        _escolher_receita()           # ja com a janela de verdade na frente
        if narrador:
            narrador.comecar()
        elif falar:
            try:
                (falar_aviso or falar)(
                    "Deixa comigo. Nao mexa no mouse nem no teclado enquanto eu trabalho. "
                    "Se quiser que eu pare na hora, aperte ESC.")
            except Exception:
                pass
        # O AVISO TOCA SOZINHO, ATE O FIM, antes do primeiro movimento. Ver
        # `_esperar_a_voz`: comecar a foto e a VPS com ele tocando era o que
        # picotava a frase.
        _esperou = _esperar_a_voz(falando, parar_agora)
        if _esperou >= 0.5:
            log(f"[usar_computador] esperei o aviso terminar ({_esperou:.1f}s)")
        if parar_agora():
            return _parei()

        if passos_receita:
            log(f"[usar_computador] receita '{nome_receita}': {len(passos_receita)} passos")
            for passo_r in passos_receita:
                if parar_agora():
                    return _parei()
                try:
                    feito = _executar(passo_r, ctx)
                except Exception as exc:
                    ctx["ultimo_ok"] = False
                    feito = f"{passo_r.get('acao')} deu erro: {str(exc)[:80]}"
                historico.append(f"receita '{nome_receita}': {feito}")
                log(f"[usar_computador]   {feito}")
                if parar_agora():
                    return _parei()
                if not ctx.get("ultimo_ok", True):
                    # PARA A RECEITA NO PRIMEIRO TROPECO. No teste com o cliente
                    # (03/09) a receita nao achou o campo da placa, seguiu assim
                    # mesmo e digitou "ORP8F38" dentro do chat do proprio Bob.
                    # Receita e atalho: quando o atalho nao bate, quem resolve e
                    # a visao, que ENXERGA a tela que esta ali.
                    historico.append(f"receita '{nome_receita}' parou aqui; "
                                     f"daqui em diante e olhando a tela")
                    log(f"[usar_computador] receita parou no tropeco — segue pela visao")
                    break
                _dormir(0.3, ctx)
            if depois_da_receita == "fim" and ctx.get("ultimo_ok", True):
                return f"Pronto, {nome_receita} feito."
            # Senao segue para o laco: a visao confere o que a receita fez e
            # termina o resto (o que muda de tela para tela).

        # ── A TAREFA SO ACABA DEPOIS DE CONFERIDA (07/09/2026) ────────
        #
        # Pedido do Diego, com o caso na mao: ele mandou desabilitar as
        # notificacoes do Windows Defender; o JARVIS desligou o PRIMEIRO
        # interruptor da pagina, disse "as notificacoes foram desabilitadas" e
        # saiu. Na tela ficaram ligados os de protecao de conta e os do
        # firewall. A regra que ele deu:
        #
        #   "toda vez que o cliente pedir alguma coisa, o Codex CLI tem de ter
        #    certeza de que foi concluido... e caso ele faca errado, o Codex
        #    corrige; e sempre certifique que nao mexeu em nada fora do que era
        #    pra ele fazer"
        #
        # Como e feito: quando o Codex diz "concluido", em vez de acreditar, eu
        # devolvo a ELE uma foto nova da tela com a cobranca de conferir item
        # por item. Se faltou, ele continua; se estiver tudo certo, responde
        # concluido outra vez e ai sim acabou. Nao inventei canal novo — e uma
        # volta a mais do laco que ja existe, entao ele CONFERE OLHANDO, nao de
        # memoria (que e o que o fez errar).
        #
        # O contador zera sempre que ele mexe em alguma coisa: se a conferencia
        # achou algo faltando e ele foi consertar, a conclusao seguinte tambem
        # e conferida. Quem segura o laco e o teto de passos, como antes.
        # `conferir_agora` e o gatilho de UMA conferencia; `conferencias` e o
        # teto de vezes na tarefa inteira, para nao virar vaivem.
        conferir_agora = True
        conferindo = False        # o passo ATUAL e de so-olhar?
        conferencias = 0
        # Eram 6. Sem relogio na tarefa (22/09/2026), o que sobra para segurar
        # a conferencia e este numero — e conferir de novo custa ~6 s, enquanto
        # entregar errado custa a tarefa inteira.
        MAX_CONFERENCIAS = 10
        # A prova desta rodada de conferencia: ele chegou a rolar a pagina? a
        # ler o estado com um comando? Ver a exigencia no fecho.
        conferencia_rolou = False
        conferencia_provou = False
        provas_cobradas: set = set()
        # A ultima conclusao boa ("pronto, liguei tudo") e se a conferencia ja
        # disse uma vez que nao conseguia olhar. Ver o tratamento do "falhou"
        # dentro da conferencia.
        ultima_conclusao = ""
        conferencia_falhou_uma_vez = False
        passo_do_ultimo_rolar = -99
        estados_antes = None
        reapareceu = {"v": False}
        # AS DUAS REGRAS QUE ELE MAIS COBRA, ditas de saida (07/09/2026).
        # Vao FATIADAS: com os enderecos do Windows juntos isto passa de 2.600
        # letras, e a VPS corta cada linha do historico em 1.800 — o fim das
        # regras (a de nao clicar duas vezes, e a de que nao ha mais relogio)
        # simplesmente nao chegava.
        # AS REGRAS DO DONO (07/09 a 22/09/2026), ENXUGADAS EM 23/09/2026.
        #
        # O texto antigo tinha ~3.800 letras, metade delas historias do teste
        # do Defender, e SE CONTRADIZIA com as regras da VPS ("junte ate 4" la,
        # "um por vez" aqui) — e mandava usar `abrir_config`, que a VPS
        # descartava. As oito regras continuam as mesmas; o que saiu foi a
        # historia de cada uma. Elas vao em DOIS lugares de proposito:
        #   - no campo `regras` (VPS v3: entra no comeco de cada conversa e
        #     nunca rola para fora);
        #   - no comeco do historico, ate a VPS responder que e v3 (VPS antiga
        #     so conhece o historico). Ver `vps_v3` no laco.
        regras_v3: list = []
        texto_regras = (
            "REGRAS DO DONO PARA ESTA TAREFA:\n"
            "A) TUDO QUER DIZER TUDO: se o pedido fala em todas/tudo/geral, o "
            "interruptor principal nao basta — role a pagina inteira ate o fim "
            "e trate cada grupo e cada item da lista.\n"
            "B) NAO ENCOSTE EM NADA FORA DO PEDIDO ('Nao incomodar', tema, outra "
            "configuracao, janela do dono). Mexeu sem querer: desfaca na hora.\n"
            "C) CONFIGURACAO DO WINDOWS: abra com {\"acao\":\"abrir_config\","
            "\"pagina\":\"notificacoes\"} (eu aperto Win+R e digito o endereco "
            "exato). E PROIBIDO chegar la pela busca do menu Iniciar ou pela "
            "busca de dentro das Configuracoes. Enderecos:\n" + _ENDERECOS_DO_WINDOWS + "\n"
            "D) ROLE DE POUCO EM POUCO (5 por vez) e faca o que tem de ser feito "
            "em cada tela antes de rolar mais — o que passa sem ser visto nao "
            "e feito.\n"
            "E) NAO CLIQUE DUAS VEZES NO MESMO INTERRUPTOR: eu anoto cada item "
            "que mudou de estado e BLOQUEIO o clique que desfaria o que ja esta "
            "como o dono pediu.\n"
            "F) SEM RELOGIO: a tarefa pode levar o tempo que precisar; faca "
            "direito ate o fim.\n"
            "G) O DONO CONFERE PELA TELA, NAO PELO REGISTRO: se existe "
            "interruptor na tela, mexa no interruptor. Se mexeu por "
            "registro/politica, abra a tela e confira; se ela nao refletir "
            "(algumas so valem depois de reiniciar), diga isso ao dono com "
            "{\"acao\":\"responder\"} em vez de dar por feito.\n"
            "H) UM MOVIMENTO POR RESPOSTA (a unica dupla aceita e digitar + "
            "Enter): eu faco, tiro foto NOVA e te mando; se vier mais de um, "
            "so o primeiro e feito.")
        pedacos_regras = _fatiar(texto_regras)
        historico.extend(pedacos_regras)
        regras_v3.extend(pedacos_regras)
        # O caminho das notificacoes do Defender, so quando o pedido e esse
        # (23/09/2026: ele ia na pagina de notificacoes do SISTEMA).
        _t_sem = _sem_acento_simples((tarefa or "").lower())
        # Sem exigir "notific": o ouvido escreve "notoficacoes", "notificassoes"...
        # e foi assim que em 23/09 a receita ficou de fora e ele passou nove
        # passos procurando o Defender nas notificacoes do SISTEMA.
        if _FALA_DO_DEFENDER.search(_t_sem):
            historico.append(_RECEITA_NOTIFICACOES_DEFENDER)
            regras_v3.append(_RECEITA_NOTIFICACOES_DEFENDER)
        # Liga ou desliga? Ver `_clique_contra_o_pedido`.
        intencao_da_tarefa = _intencao_da_tarefa(tarefa)
        if intencao_da_tarefa:
            log(f"[usar_computador] o pedido e {intencao_da_tarefa.upper()}: interruptor que "
                f"ja estiver assim nao sera clicado")
        # ONDE FICA CADA COISA NESTE PC. Em TODA tarefa — ver `_onde_e_este_pc`.
        try:
            pedacos_pc = _fatiar(_onde_e_este_pc())
            historico.extend(pedacos_pc)
            regras_v3.extend(pedacos_pc)
        except Exception as exc:
            log(f"[usar_computador] nao montei os caminhos: {str(exc)[:80]}")
        # A CAIXA DE FERRAMENTAS DE CONSERTO, so quando a tarefa e de conserto.
        # Em tarefa comum ela so gastaria espaco no pedido.
        if _TAREFA_DE_CONSERTO.search(tarefa or ""):
            pedacos = _manual_para(tarefa)
            historico.extend(pedacos)
            regras_v3.extend(pedacos)
            log(f"[usar_computador] tarefa de conserto: manual em {len(pedacos)} "
                f"pedaco(s), {sum(len(p) for p in pedacos)} letras"
                f"{' (roteiro de PROGRAMA QUE NAO ABRE)' if _NAO_ABRE.search(tarefa or '') else ''}")
        # Quantas entradas do comeco sao REGRA e vao em TODO pedido (as outras
        # rolam). Ver `_historico_para_a_vps` — sem isto elas sumiam no passo 6.
        fixas = len(historico)
        # O CADERNO DO MODELO (VPS v3): o que ele anotou como feito / falta /
        # dados lidos. Volta para ele em todo passo — e e o que ele le quando a
        # conversa e renovada (a cada 6 passos, porque as fotos pesam).
        memoria_modelo = ""
        vps_v3 = False
        _registrar_passo(f"=== TAREFA: {tarefa[:300]}")
        for passo in range(1, MAX_PASSOS + 1):
            if narrador:
                narrador.andamento(passo, MAX_PASSOS)
            if parar_agora():
                return _parei()
            # Um lembrete de 2 em 2 minutos pode estar tocando: a foto e o
            # pedido a VPS esperam ele acabar. Ver `_esperar_a_voz`.
            if _esperar_a_voz(falando, parar_agora) >= 0.5 and parar_agora():
                return _parei()
            # SEM RELOGIO (22/09/2026). `TEMPO_MAXIMO_S` e None no uso normal e
            # so existe para um teste poder cronometrar. Ver a nota la em cima.
            if TEMPO_MAXIMO_S and time.monotonic() - t0 > TEMPO_MAXIMO_S:
                return ("Passei do tempo limite e parei no meio. Me diz com mais detalhe "
                        "o que voce quer que eu faca e eu tento de novo.")
            # Tarefa comprida: um sinal de vida a cada 3 minutos, para o dono
            # saber que ele continua trabalhando (e nao travado). Custa nada.
            agora_m = time.monotonic()
            if agora_m - avisou_vivo_em > 180:
                avisou_vivo_em = agora_m
                minutos = int((agora_m - t0) // 60)
                if minutos >= 3:
                    log(f"[usar_computador] {minutos} min, passo {passo}, ainda trabalhando")
                    if narrador:
                        try:
                            narrador.marco(f"Ainda estou nisso, chefe. "
                                           f"{minutos} minutos, passo {passo}.")
                        except Exception:
                            pass

            # 0. O Windows pediu permissao de administrador? Ninguem clica
            # naquela tela por fora — nem o Bob. Pede ao dono e espera.
            if _uac_aberto():
                if falar:
                    try:
                        falar("O Windows esta pedindo permissao de administrador. "
                              "Clica em Sim, chefe, que eu continuo daqui.")
                    except Exception:
                        pass
                esperou = 0.0
                while _uac_aberto() and esperou < 90 and not parar_agora():
                    time.sleep(1.0)
                    esperou += 1.0
                if _uac_aberto():
                    return ("O Windows ficou pedindo permissao de administrador e eu nao "
                            "posso clicar naquela tela. Confirma voce e me pede de novo.")
                historico.append("o Windows pediu permissao de administrador e o dono respondeu")
                _dormir(1.5, ctx)

            # 1. OLHAR (ampliado, se o passo anterior pediu `ampliar`)
            pedido_zoom = ctx.pop("ampliar", None)
            ctx.pop("enter_ja_apertado", None)     # so vale dentro do mesmo passo
            try:
                olhada = tela_visao.olhar(regiao=pedido_zoom)
            except Exception as exc:
                log(f"[usar_computador] olhar falhou: {str(exc)[:120]}")
                return f"Nao consegui enxergar a tela: {str(exc)[:70]}"

            # O dono encostou no mouse? Fica ANOTADO para o Codex saber que a
            # tela pode ter mudado por fora — mas NAO se fala nada: a regra do
            # cliente e silencio entre o aviso do comeco e o resultado do fim.
            try:
                import pyautogui  # noqa: PLC0415
                agora_mouse = pyautogui.position()
                esperado = ctx.get("mouse_esperado")
                if (esperado
                        and (abs(agora_mouse[0] - esperado[0]) + abs(agora_mouse[1] - esperado[1])) > 90
                        and time.monotonic() - avisou_mouse_em > 30):
                    avisou_mouse_em = time.monotonic()
                    historico.append("o dono encostou no mouse; a tela pode ter mudado por fora")
            except Exception:
                pass

            # MUDOU? Duas provas, e basta uma: o desenho (pedaco que mais mudou)
            # ou a ARVORE (elementos que apareceram/sumiram). No Simplo a
            # sanfona abria, a lista crescia, e so a segunda prova pegava —
            # a primeira versao desistia dizendo "a tela parou de reagir" no
            # meio de uma tarefa que estava indo bem.
            impressao = tela_visao.impressao_dos_elementos(olhada["elementos"])
            # TERCEIRA PROVA: O FOCO (04/09/2026). Na arvore do Visualizador de
            # Eventos o JARVIS andava de ramo em ramo e as duas provas de cima nao
            # viam nada — o desenho mal muda (uma linha fica azul) e a lista de
            # elementos e identica. Ele desistiu dizendo "a tela parou de
            # reagir" numa tela que estava reagindo, com o dono sem tocar em
            # nada. Selecao e foco mudam nesses casos, e o Windows conta de
            # graca. Basta UMA das tres provas para a tela ter mudado.
            foco = tela_visao.foco_agora()
            mudou = True
            if assinatura_anterior is not None:
                mudou = ((tela_visao.diferenca(assinatura_anterior, olhada["assinatura"])
                          >= tela_visao.LIMIAR_MUDANCA)
                         or impressao != impressao_anterior
                         or (foco and foco != foco_anterior))
            assinatura_anterior = olhada["assinatura"]
            impressao_anterior = impressao
            foco_anterior = foco

            # O QUE ELE ACABOU DE MEXER — e a autocorrecao pedida pelo Diego.
            # A comparacao e feita AQUI, entre a tela de antes e a de agora, e o
            # resultado vai no historico, que o Codex sempre le. Assim ele nao
            # precisa lembrar do que fez: esta escrito.
            _manter_fora_da_frente(log, reapareceu)
            estados_agora = _estados_da_tela(olhada["elementos"])
            if estados_antes is not None:
                virou = _virou_o_que(estados_antes, estados_agora)
                if virou:
                    historico.append(
                        "MUDOU DE ESTADO no seu ultimo movimento: "
                        + "; ".join(virou)
                        + ". CONFIRA: isso fazia parte do pedido do dono? Se "
                          "NAO fazia, desfaca agora, antes de seguir.")
                    log(f"[usar_computador] mudou: {'; '.join(virou)[:120]}")
                # O CADERNINHO DO QUE JA FOI MEXIDO. E o que impede, mais
                # tarde, o clique que desfaz — ver `_desfaria`.
                for rotulo, agora_est in estados_agora.items():
                    antigo = estados_antes.get(rotulo)
                    if antigo and antigo != agora_est:
                        mexidos[_chave_do_item(rotulo)] = (antigo, agora_est, passo)
            estados_antes = estados_agora
            sem_mudar = 0 if mudou else sem_mudar + 1
            # CLIQUE CEGO EM x,y E O QUE MAIS TRAVA (04/09/2026). No teste do
            # Visualizador de Eventos, sete dos nove movimentos foram cliques em
            # coordenada, sempre no mesmo cantinho da arvore, tentando acertar a
            # setinha de 12 px. Quando a tela nao reage e os ultimos movimentos
            # foram assim, o proximo pedido leva o recado no historico — que o
            # Codex sempre le — em vez de so dizer "nao mudou".
            if not mudou and _so_clique_cego(ultimos_movimentos):
                nomes = [f'"{e["nome"]}"' for e in olhada["elementos"]
                         if e.get("tipo") in ("item", "aba", "botao", "link") and e.get("nome")][:8]
                historico.append(
                    "AVISO: seus ultimos cliques foram em coordenada x,y e a tela nao reagiu. "
                    "Nesta tela ha itens COM NOME — pare de clicar em pixel e use "
                    "expandir/clicar_nome/acionar com o nome, ou o teclado (setas, enter). "
                    + (f"Itens visiveis agora: {', '.join(nomes)}." if nomes else ""))
            if sem_mudar >= SEM_MUDANCA_LIMITE:
                return ("Tentei de varios jeitos e a tela nao reagiu a nenhum deles — o erro e meu, "
                        "nao seu; provavelmente eu estava clicando no lugar errado. "
                        "Me diz de outro jeito o que voce precisa, ou abre a janela certa que eu continuo.")
            # COMANDO NAO MEXE NA TELA, E ISSO NAO E A TELA TRAVADA (22/09/2026).
            #
            # No 4o teste do Roblox ele fez a coisa certa: foi direto a pasta
            # certa e passou onze passos DIAGNOSTICANDO por comando. Como
            # comando nao muda a tela, o contador de "a tela nao reagiu" subiu a
            # cada passo e no sexto eu matei a tarefa dizendo "tentei de varios
            # jeitos e a tela nao reagiu" — no meio de um diagnostico que estava
            # indo bem.
            #
            # Aquele contador existe para o clique que erra o alvo. Passo que
            # so roda comando nao entra nele.
            if so_comando_no_ultimo_passo:
                sem_mudar = 0

            ctx.update({"escala_x": olhada["escala_x"], "escala_y": olhada["escala_y"],
                        "desloc_x": olhada.get("desloc_x", 0), "desloc_y": olhada.get("desloc_y", 0),
                        "hwnd_da_foto": olhada.get("hwnd") or 0,
                        "por_numero": {el["n"]: el for el in olhada["elementos"]}})

            # A INVESTIGACAO PRONTA, UMA VEZ SO, NO PRIMEIRO OLHAR.
            # Ver `_investigar_erro_da_tela`: quando a tela diz que falta um
            # arquivo, o programa acha o arquivo antes de o Codex comecar.
            if passo == 1 and not investigado["v"]:
                investigado["v"] = True
                achado = _investigar_erro_da_tela(olhada, log)
                if achado:
                    # ENTRA NAS FIXAS, NAO NO CORPO (22/09/2026). Este era o
                    # defeito do 6o teste: o diagnostico era acrescentado aqui,
                    # DEPOIS de `fixas` ja estar contado, entao entrava na
                    # parte do historico que ROLA — e sumia em poucos passos.
                    # No log dava para ver o efeito: "investiguei antes: falta
                    # Qt5Widgets.dll; 1 pista" e, logo depois, ele procurando o
                    # mesmo arquivo do zero por trinta passos.
                    pedacos_do_achado = _fatiar(achado)
                    historico[fixas:fixas] = pedacos_do_achado
                    fixas += len(pedacos_do_achado)
                    regras_v3.extend(pedacos_do_achado)

            # O QUE AS CAIXAS DE ERRO DIZEM, EM TEXTO (22/09/2026).
            #
            # A varredura de tela le a janela em primeiro plano; uma caixa de
            # erro do Windows e outra janela, e o texto dela nunca chegava ao
            # Codex — ele so tinha a foto. Medido com a caixa do Roblox aberta:
            # o titulo ia, a mensagem nao. Agora vai a mensagem inteira, lida
            # do proprio Windows.
            caixas_agora = _caixas_de_erro()
            if caixas_agora:
                linhas_caixa = [f'  "{t}" -> {c[:300]}' for _h, t, c in caixas_agora[:4]]
                historico.append(
                    f"HA {len(caixas_agora)} CAIXA(S) DE ERRO ABERTA(S) NA TELA "
                    f"AGORA, e isto e o que elas dizem (texto lido do proprio "
                    f"Windows, nao da foto):\n" + "\n".join(linhas_caixa)
                    + "\n  Para fechar TODAS de uma vez use "
                      '{"acao":"fechar_erros"} — nao adianta clicar em OK uma '
                      "por uma, porque cada tentativa de abrir o programa cria "
                      "outra. Feche-as, conserte a causa, e so depois abra o "
                      "programa de novo para conferir.")

            # DIAGNOSTICAR DEMAIS TAMBEM E NAO FAZER NADA (22/09/2026).
            #
            # No 5o teste ele deu 60 passos e mais de 50 foram comandos de
            # LEITURA, quase todos a mesma busca com outra roupa. Procurar e
            # certo; procurar a mesma coisa vinte vezes e laco.
            #
            # A conta e simples: passou de `TETO_DE_BUSCAS` comandos de leitura
            # seguidos sem UM movimento que mude alguma coisa, ele e cobrado.
            if buscas_seguidas == TETO_DE_BUSCAS:
                historico.append(
                    f"PARE DE PROCURAR. Voce ja rodou {buscas_seguidas} comandos "
                    f"de leitura seguidos sem consertar nada. Repetir a busca "
                    f"com outras palavras nao vai mudar a resposta.\n"
                    f"AGORA, no proximo movimento, faca UMA destas tres coisas:\n"
                    f"1) CONSERTE com o que voce ja sabe (o comando que muda "
                    f"alguma coisa, ou o clique);\n"
                    f"2) se falta um dado que so a tela mostra, CLIQUE/ABRA a "
                    f"tela que mostra esse dado;\n"
                    f"3) se voce realmente nao consegue, responda 'falhou' "
                    f"dizendo o que ja descobriu e o que falta — o dono prefere "
                    f"isso a mais dez buscas.\n"
                    f"E se for procurar arquivo de novo: nome EXATO nao acha "
                    f"arquivo RENOMEADO. Use -Filter 'nome.*' com asterisco.")
                log(f"[usar_computador] {buscas_seguidas} buscas seguidas; cobrei acao")

            # 1b. O DONO CORRIGIU NO MEIO? (03/09/2026, pedido do cliente)
            # Ele esta vendo a tela: se o Bob foi para o lugar errado, ele fala
            # e a correcao entra AQUI, sem parar a tarefa e sem recomecar. Vai
            # em dois lugares de proposito: no historico (o Codex ve agora) e no
            # texto da tarefa (sobrevive a troca de thread, que acontece a cada
            # 6 passos e reapresenta so a tarefa).
            if correcoes:
                try:
                    novas = correcoes() or []
                except Exception:
                    novas = []
                for c in novas:
                    c = str(c).strip()[:300]
                    if not c:
                        continue
                    correcoes_ditas.append(c)
                    historico.append(f"CORRECAO DO DONO (obedeca imediatamente): {c}")
                    log(f"[usar_computador] correcao do dono: {c[:80]}")
                if novas:
                    # Com os caminhos desta maquina junto — senao a correcao do
                    # dono refazia a tarefa SEM eles, e ele voltava a chutar
                    # nome de usuario no meio do servico.
                    base = _tarefa_completa(dicas)
                    lista = "\n- ".join(correcoes_ditas[-5:])
                    tarefa_vps = (
                        f"{base}\n\nCORRECOES DO DONO durante a execucao "
                        f"(mandam mais que o pedido original; a ultima e a que vale):\n- {lista}")

            # 2. DECIDIR (na VPS, pelo Codex)
            t1 = time.monotonic()
            # VPS v3: as regras vao no campo `regras` e o historico leva so o
            # relato dos passos (o mais novo inteiro). VPS antiga (ou antes de
            # saber): tudo no historico, como sempre foi.
            if vps_v3:
                hist_envio = _historico_para_a_vps(historico, fixas, inteiras=6)[fixas:]
            else:
                hist_envio = _historico_para_a_vps(historico, fixas)
            regiao_foto = olhada.get("regiao")
            try:
                resposta = _pedir_passo(
                    relay,
                    job_id=job_id, tarefa=tarefa_vps, passo=passo, jpeg=olhada["jpeg"],
                    largura=olhada["largura"], altura=olhada["altura"],
                    historico=hist_envio,
                    regras=regras_v3, memoria=memoria_modelo,
                    historico_seq=len(historico) - fixas,
                    elementos=tela_visao.resumo_dos_elementos(olhada["elementos"]),
                    contexto={"janela": olhada["titulo"], "janelas": olhada["janelas"],
                              "mudou": bool(mudou),
                              "foco": foco,
                              "ampliada": (f"({regiao_foto[0]},{regiao_foto[1]})-({regiao_foto[2]},{regiao_foto[3]}) da tela"
                                           if regiao_foto else ""),
                              # O QUE ESTE CLIENTE SABE FAZER ALEM DO BASICO.
                              # A VPS e a MESMA para todos os clientes, e o Bob
                              # da TJ ja esta entregue: se o Codex mandasse
                              # `arrastar_para` para ele, o PC dele responderia
                              # "movimento desconhecido" e a volta (7 s) seria
                              # perdida. Por isso quem declara e o cliente — a
                              # VPS so oferece ao modelo o que chegou aqui.
                              "recursos": list(RECURSOS),
                              "repetindo": len(ultimos_movimentos) >= 3
                                           and len(set(ultimos_movimentos[-3:])) == 1},
                    timeout=95.0, cancelar=parar_agora)
            except Exception as exc:
                # ESC (ou "para") no meio da espera pela VPS: sai na hora, sem
                # contar como falha do servidor. Ver `pedir_passo_na_tela`.
                if parar_agora():
                    return _parei()
                # ── O TETO DE PASSOS NAO E DAQUI: E DA VPS (22/09/2026) ─────
                #
                # No teste do Roblox Studio o dono viu "limite de passos" e a
                # tarefa morrer no passo 60 — mesmo com o relogio ja removido e
                # `MAX_PASSOS = 600` aqui. O teto que estourou e o da VPS, que
                # conta os passos POR JOB e recusa o pedido seguinte. Do lado
                # do cliente aquilo chegava como excecao, e tres seguidas
                # encerravam a tarefa: "o servidor falhou tres vezes".
                #
                # Nao ha nada de errado em estourar o teto de um job — so quer
                # dizer que a tarefa e longa. Entao, em vez de desistir, o
                # cliente ABRE UM JOB NOVO e continua de onde parou: a tela e a
                # mesma, o historico e o mesmo, e o Codex recebe o recado de
                # que a contagem recomecou. Para o dono, nada acontece.
                if _E_TETO_DA_VPS.search(str(exc)):
                    trocas_de_job += 1
                    job_id_antigo = job_id
                    job_id = secrets.token_hex(12)
                    log(f"[usar_computador] a VPS fechou o job no passo {passo} "
                        f"(teto dela); abri outro e continuo ({trocas_de_job})")
                    try:
                        relay.encerrar_passos_na_tela(job_id_antigo)
                    except Exception:
                        pass
                    historico.append(
                        f"AVISO DE SISTEMA: a contagem de passos do servidor "
                        f"chegou ao fim e eu abri uma contagem nova. A TAREFA "
                        f"CONTINUA EXATAMENTE DE ONDE PAROU — nao recomece do "
                        f"zero, nao repita o que ja esta feito, olhe a tela e "
                        f"siga do ponto em que voce estava.")
                    erros_seguidos = 0
                    _dormir(0.5, ctx)
                    continue
                # UM PASSO COM ERRO NAO ENCERRA A TAREFA (03/09/2026). Antes,
                # qualquer tropeco da VPS — inclusive "o modelo nao respondeu um
                # movimento valido", que e passageiro — derrubava tudo e o dono
                # ouvia "perdi a ligacao com o servidor" no meio do servico.
                # Agora ele tenta de novo, com a falha anotada no historico
                # para o Codex saber o que aconteceu. So desiste depois de tres
                # seguidas, que ai e a ponte fora do ar mesmo.
                # ── "O MODELO NAO RESPONDEU UM MOVIMENTO VALIDO" (22/09/2026)
                #
                # Tres delas seguidas encerravam a tarefa, e o dono ouvia "o
                # servidor falhou tres vezes" — que soa como ponte caida e nao
                # e. Esse aviso quer dizer outra coisa: o Codex respondeu, mas
                # a resposta nao virou movimento nenhum. A causa que vimos foi
                # JSON quebrado por comando de PowerShell cheio de aspas
                # (`$_`, `{}`, aspas dentro de aspas) — e isso se resolve
                # pedindo um comando MAIS SIMPLES, nao desistindo.
                #
                # Agora ele tem 6 tentativas, e a partir da segunda o recado
                # diz o que fazer diferente. So desiste se for sempre o mesmo
                # tipo de tropeco, e ai a frase final explica o que houve.
                erros_seguidos += 1
                # ERRO SEM MENSAGEM tambem e recuperavel (22/09/2026). No 6o
                # teste o passo 1 falhou com a mensagem VAZIA — e um tropeco da
                # ponte, nao ponte caida. Antes ele contava no teto de 3, e
                # tres desses encerravam a tarefa sem o dono entender por que.
                so_resposta_ruim = (bool(_RESPOSTA_SEM_MOVIMENTO.search(str(exc)))
                                    or not str(exc).strip())
                if not str(exc).strip():
                    log(f"[usar_computador] passo {passo}: a ponte devolveu erro "
                        f"SEM MENSAGEM ({type(exc).__name__}); tratando como tropeco")
                teto_de_erros = 6 if so_resposta_ruim else 3
                log(f"[usar_computador] passo {passo} falhou "
                    f"({erros_seguidos}/{teto_de_erros}): {str(exc)[:140]}")
                if so_resposta_ruim:
                    historico.append(
                        f"passo {passo}: a sua resposta anterior nao virou "
                        f"movimento nenhum aqui. Quase sempre e JSON quebrado "
                        f"por um comando comprido demais, com aspas dentro de "
                        f"aspas. FACA DIFERENTE AGORA: um comando CURTO e "
                        f"SIMPLES, de uma linha so, com aspas simples por "
                        f"fora; sem $_, sem chaves, sem ponto-e-virgula "
                        f"encadeando varias coisas. Se precisar de mais de um, "
                        f"mande um por passo.")
                else:
                    historico.append(f"passo {passo}: o servidor nao respondeu direito "
                                     f"({str(exc)[:70]}); tentando de novo")
                if erros_seguidos >= teto_de_erros:
                    if so_resposta_ruim:
                        return ("Eu travei numa parte da tarefa: o servidor respondeu, "
                                "mas sem um movimento que eu conseguisse executar. "
                                "Me diz de outro jeito o que voce precisa que eu tento "
                                "de novo.")
                    return (f"O servidor falhou tres vezes seguidas e eu parei: {str(exc)[:70]}")
                _dormir(1.0 if so_resposta_ruim else 1.5, ctx)
                continue
            dt = time.monotonic() - t1
            erros_seguidos = 0
            if not isinstance(resposta, dict):
                return "O servidor nao respondeu de um jeito que eu entenda."

            acoes = _lista_de_acoes(resposta)
            fala_geral = str(resposta.get("fala") or "").strip()
            # O QUE ELE VIU E COMO AVALIOU O ULTIMO MOVIMENTO (VPS v3). O caderno
            # (`memoria`) volta para ele no proximo passo; o resto vai para o
            # registro, que e onde se descobre POR QUE ele errou.
            if resposta.get("v") == 3 and not vps_v3:
                vps_v3 = True
                log("[usar_computador] VPS v3: regras em campo proprio, caderno ligado")
            _memoria_nova = str(resposta.get("memoria") or "").strip()
            if _memoria_nova:
                memoria_modelo = _memoria_nova[:1500]
            _vi = str(resposta.get("vi") or "").strip()
            _aval = str(resposta.get("avaliacao") or "").strip()
            log(f"[usar_computador] passo {passo}: {len(acoes)} movimento(s) em {dt:.1f}s "
                f"| {str(acoes)[:180]}")
            if _vi or _aval:
                log(f"[usar_computador]   viu: {_vi[:140]} | {_aval[:90]}")
            _registrar_passo(f"passo {passo} ({dt:.1f}s) janela='{olhada['titulo'][:50]}'"
                             f"{' AMPLIADA' if regiao_foto else ''} mudou={bool(mudou)} | viu: {_vi[:300]}"
                             f" | avaliou: {_aval[:200]} | caderno: {memoria_modelo[:300]}"
                             f" | mandou: {json.dumps(_acoes_pedidas(resposta), ensure_ascii=False)[:500]}")

            if not acoes:
                historico.append(f"passo {passo}: a VPS nao mandou movimento nenhum")
                continue

            # Ele mandou um lote? So o primeiro vai, e ELE PRECISA SABER disso:
            # sem este recado ele acharia que os outros cliques tambem foram
            # feitos e seguiria o plano velho. Ver `MAX_ACOES_POR_PASSO`.
            _pedidas = _acoes_pedidas(resposta)
            if len(_pedidas) > len(acoes):
                _sobra = ", ".join(
                    f"{a.get('acao')} "
                    f"{_alvo_do_clique(a) or (('elemento ' + str(a.get('elemento'))) if a.get('elemento') is not None else '')}".strip()
                    for a in _pedidas[len(acoes):])[:300]
                historico.append(
                    f"passo {passo}: UM MOVIMENTO POR VEZ — voce mandou {len(_pedidas)} "
                    f"movimentos juntos e eu fiz SO o primeiro. NAO foram feitos: {_sobra}. "
                    "A proxima foto mostra a tela DEPOIS desse movimento: olhe o que "
                    "mudou e mande o proximo, UM so.")
                log(f"[usar_computador]   um por vez: {len(_pedidas) - len(acoes)} "
                    f"movimento(s) ficaram para depois da foto")

            # 3. FAZER (ate 4 movimentos, na ordem, parando no primeiro problema)
            #
            # ...MENOS quando o passo e de CONFERENCIA: ali ele so olha.
            if conferindo:
                proibidas = [a for a in acoes
                             if str(a.get("acao") or "").lower().strip()
                             not in _ACOES_DE_OLHAR or not _comando_so_le(a)]
                # Rolar durante a conferencia e o que prova que ele olhou a
                # pagina inteira — ver a exigencia no fecho da tarefa.
                for a in acoes:
                    if str(a.get("acao") or "").lower().strip() in (
                            "rolar", "scroll", "rolar_ate", "rolar_ate_ver"):
                        conferencia_rolou = True
                    if str(a.get("acao") or "").lower().strip() in (
                            "comando", "powershell", "cmd", "executar_comando", "rodar"):
                        conferencia_provou = True
                if proibidas:
                    quais = ", ".join(sorted({str(a.get("acao")) for a in proibidas}))
                    historico.append(
                        f"BLOQUEEI o seu movimento ({quais}): este passo e de "
                        "CONFERENCIA, e nele voce SO OLHA. Nao clique, nao "
                        "digite, nao tecle — clicar de novo num interruptor que "
                        "voce ja mexeu DESFAZ o que estava certo, e foi isso que "
                        "estragou a tarefa passada. Role a pagina se precisar "
                        "enxergar mais, e me RESPONDA: esta tudo feito? Se "
                        "faltou algo, diga O QUE falta e onde — no passo "
                        "seguinte eu libero voce para corrigir.")
                    log(f"[usar_computador] conferencia: bloqueei {quais}")
                    conferindo = False      # o proximo passo ja pode agir
                    continue
            terminou = None
            terminou_falhou = False
            for acao in acoes:
                if parar_agora():
                    return _parei()
                tipo = str(acao.get("acao") or "").lower().strip()
                fala = str(acao.get("fala") or "").strip() or fala_geral

                # INTERRUPTOR QUE JA ESTA COMO O DONO PEDIU NAO SE CLICA
                # (23/09/2026). Ver `_clique_contra_o_pedido`.
                if not correcoes_ditas:
                    contra = _clique_contra_o_pedido(acao, ctx, intencao_da_tarefa)
                    if contra:
                        historico.append(contra)
                        log(f"[usar_computador] bloqueei clique contra o pedido: "
                            f"{_alvo_do_clique(acao) or acao.get('elemento')}")
                        _registrar_passo(f"   BLOQUEADO: {contra[:200]}")
                        break     # olha a tela de novo antes do proximo movimento

                # NAO DESFAZ O QUE JA ESTA CERTO (22/09/2026). Ver `_desfaria`.
                # A frase da acao pode PEDIR para desfazer ("o dono mandou
                # religar") — nesse caso passa.
                if mexidos and not _PEDE_DESFAZER.search(fala or ""):
                    desfaz, recado_trava = _desfaria(acao, mexidos, estados_agora)
                    if desfaz:
                        historico.append(recado_trava)
                        log(f"[usar_computador] bloqueei um clique que desfazia: "
                            f"{_alvo_do_clique(acao)}")
                        break     # olha a tela de novo antes do proximo movimento

                if tipo in ("concluido", "done", "pronto", "finalizado"):
                    terminou = fala or "Pronto, feito."
                    break
                if tipo in ("falhou", "fail", "erro"):
                    terminou = fala or str(acao.get("motivo") or "Nao consegui terminar isso na tela.")
                    terminou_falhou = True
                    break
                if tipo in ("responder", "explicar", "avisar"):
                    # O Codex respondendo uma duvida do dono no meio da tarefa.
                    # Fala e segue — nao encerra, nao espera resposta.
                    recado = fala or str(acao.get("texto") or "").strip()
                    # NA CONFERENCIA O "RESPONDER" E RECADO DELE PARA ELE MESMO
                    # (23/09/2026). Ali ele diz o que falta e onde fica a prova
                    # — e isso saia pela caixa de som no meio da tarefa ("a
                    # prova fica na Seguranca do Windows..."). O dono so ouve
                    # o que e para ele: a resposta a uma pergunta dele e o fim.
                    if recado and falar and not conferindo:
                        try:
                            falar(recado)
                        except Exception:
                            pass
                    if conferindo:
                        historico.append(f"passo {passo}: na conferencia voce anotou: {recado[:300]}")
                        log(f"[usar_computador] conferencia anotou (sem falar): {recado[:100]}")
                    else:
                        historico.append(f"passo {passo}: respondi ao dono: {recado[:120]}")
                        log(f"[usar_computador] resposta ao dono: {recado[:100]}")
                    continue

                if tipo in ("perguntar", "ask"):
                    pergunta = fala or str(acao.get("pergunta") or "Como voce quer que eu continue?")

                    # A CAIXA DO "CONTINUAR" NAO E O UAC (04/09/2026).
                    #
                    # O Windows tem DUAS telas parecidas e so uma e intocavel:
                    #   - o UAC de verdade escurece a tela inteira, roda numa
                    #     area de trabalho segura e NINGUEM de fora clica nele
                    #     (e o `consent.exe`, que o `_uac_aberto` reconhece);
                    #   - a caixa do EXPLORADOR ("Voce precisara fornecer
                    #     permissao de administrador para...") e uma janela
                    #     comum, com um botao Continuar que qualquer um clica.
                    # O Codex confundiu as duas e ficou pedindo ao dono que
                    # clicasse — tres vezes seguidas, mesmo depois de ele
                    # autorizar. Se nao ha consent.exe rodando, a caixa e a
                    # segunda: ele proprio clica.
                    if _fala_de_permissao(pergunta) and not _uac_aberto():
                        historico.append(
                            "NAO ha tela de permissao do Windows (UAC) aberta agora — conferido no "
                            "proprio sistema. A caixa que voce esta vendo e do Explorador de "
                            "Arquivos e o botao 'Continuar' dela PODE ser clicado por voce. "
                            "Clique nele (clicar_nome com nome 'Continuar') e siga; o dono ja "
                            "autorizou a tarefa.")
                        log("[usar_computador] o Codex achou que era UAC e nao era; mandei clicar")
                        break

                    # JA AUTORIZOU: NAO PERGUNTA DE NOVO (04/09/2026).
                    # "O cliente autorizou uma vez, ele pode continuar" — pedido
                    # do Diego depois de o JARVIS perguntar quatro vezes na mesma
                    # tarefa se podia seguir.
                    familia_perg = _familia_do_risco(pergunta, {})
                    if _pede_permissao(pergunta) and (
                            familia_perg in autorizados
                            or (familia_perg == "outro" and autorizados)):
                        historico.append(
                            f"passo {passo}: voce perguntou '{pergunta[:60]}' — o dono JA AUTORIZOU "
                            f"isso nesta tarefa ({', '.join(sorted(autorizados))}). SIGA sem "
                            f"perguntar de novo; so volte a perguntar se aparecer um risco de "
                            f"outro tipo.")
                        log(f"[usar_computador] pergunta ja autorizada, respondi sozinho: {pergunta[:70]}")
                        break

                    resposta_dono = perguntar_seguro(pergunta)
                    if not resposta_dono:
                        terminou = "Fiquei sem a sua resposta e parei por aqui."
                        break
                    # A resposta dele pode AUTORIZAR o que vier depois ("pode",
                    # "autorizo", "manda ver"): guarda, senao a proxima pergunta
                    # seria a mesma.
                    if _SIM.search(resposta_dono) and _pede_permissao(pergunta):
                        autorizados.add(familia_perg)
                    historico.append(f"passo {passo}: perguntei '{pergunta[:60]}' "
                                     f"e o dono respondeu: {resposta_dono[:120]}")
                    break     # com dado novo, olhar a tela de novo antes de agir

                # Confirmacao do que nao tem volta — UMA VEZ POR FAMILIA.
                descricao = " ".join(str(acao.get(k) or "") for k in
                                     ("fala", "texto", "alvo", "motivo", "url"))
                # COMANDO QUE SO LE NAO PEDE PERMISSAO (22/09/2026).
                #
                # No 3o teste do Roblox o dono ouviu "Posso seguir com esse
                # passo? E so dizer sim ou nao" no meio da tarefa e nao
                # entendeu — com razao: o movimento era um
                # `Get-ItemProperty HKCU:\...\Uninstall\*`, que so LISTA os
                # programas instalados. A palavra "Uninstall" no meio do
                # caminho do registro casou com a regra de perigo, e ele parou
                # a tarefa para pedir autorizacao de uma leitura.
                #
                # Ler nunca muda nada. A pergunta fica para quem MEXE.
                if _comando_so_le(acao) and str(acao.get("acao") or "").lower().strip() in (
                        "comando", "powershell", "cmd", "executar_comando", "rodar"):
                    descricao = ""
                if bool(acao.get("confirmar")) or _PERIGOSO.search(descricao) or _tecla_sem_volta(acao):
                    familia = _familia_do_risco(descricao, acao)
                    if familia in autorizados:
                        # Ja disse que sim para este tipo de coisa nesta tarefa.
                        # Perguntar de novo nao protege ninguem: so faz a tarefa
                        # morrer quando ele acha que ja respondeu (04/09/2026).
                        historico.append(f"passo {passo}: o dono ja autorizou "
                                         f"'{familia}' nesta tarefa; segui sem perguntar")
                    else:
                        # A PERGUNTA TEM DE DIZER O QUE E (22/09/2026). "Posso
                        # seguir com esse passo? E so dizer sim ou nao" nao
                        # informa nada — o dono ouviu isso no meio da tarefa do
                        # Roblox e respondeu, nas palavras dele, "nao entendi".
                        # Quem e perguntado precisa saber o que esta
                        # autorizando, senao a pergunta so atrapalha.
                        pergunta = _pergunta_clara(acao, fala, familia)
                        r = (perguntar_seguro(pergunta, 40.0, so_sim_ou_nao=True) or "")
                        # ELE PERGUNTOU DE VOLTA? Entao a resposta e uma
                        # RESPOSTA, nao um "nao". A duvida vai para o Codex no
                        # proximo passo (ele esta com a tela na frente e sabe o
                        # que ia fazer), a resposta e falada, e a confirmacao
                        # volta a ser pedida. Vale tambem quando ele diz sim E
                        # pergunta na mesma frase — a duvida nao pode se perder
                        # so porque a autorizacao veio junto.
                        if _parece_duvida(r):
                            historico.append(
                                f"PERGUNTA DO DONO, feita quando eu pedi confirmacao para "
                                f"'{fala[:60] or familia}': \"{r[:200]}\". "
                                f"RESPONDA a ele com {{\"acao\":\"responder\",\"fala\":\"...\"}} "
                                f"antes de qualquer outro movimento: curto, em portugues, dizendo o que "
                                f"esses arquivos/itens sao e se ha risco. Depois de responder, "
                                f""
                                + ("continue a tarefa (ele ja autorizou)."
                                   if _SIM.search(r) else
                                   "peca a confirmacao de novo."))
                            log(f"[usar_computador] o dono perguntou no meio: {r[:80]}")
                            if _SIM.search(r):
                                autorizados.add(familia)
                            # Sai do lote e volta a olhar a tela: o proximo
                            # passo traz a resposta do Codex para a duvida.
                            break
                        if not _SIM.search(r):
                            terminou = ("Certo, nao fiz — parei antes desse passo."
                                        if r else
                                        "Nao te ouvi responder e parei por seguranca, sem fazer esse passo.")
                            break
                        autorizados.add(familia)
                        historico.append(f"passo {passo}: o dono AUTORIZOU '{familia}': {fala[:80]}")

                if narrador and fala:
                    narrador.marco(fala)

                if tipo in ("comando", "powershell", "cmd", "executar_comando",
                            "rodar") and re.search(
                        r"(hk(lm|cu|cr|u|cc):|reg\s+add|new-itemproperty|"
                        r"set-itemproperty|registry::)",
                        str(acao.get("texto") or ""), re.I) and not _comando_so_le(acao):
                    mexeu_no_registro = True

                try:
                    feito = _executar(acao, ctx)
                except Exception as exc:
                    feito = f"{tipo} deu erro: {str(exc)[:80]}"
                # RESULTADO LONGO EM PEDACOS (23/09/2026): a saida de comando e o
                # texto lido da tela passam de 1.800 letras, e a VPS corta cada
                # linha nesse teto. Fatiado, chega inteiro.
                for _i, _pedaco in enumerate(_fatiar(f"passo {passo}: {feito}", 1750)[:4]):
                    historico.append(_pedaco if _i == 0 else f"(continua) {_pedaco}")
                _registrar_passo(f"   fez: {str(feito)[:400]}")
                if tipo in ("rolar", "scroll", "rolar_ate", "rolar_ate_ver"):
                    passo_do_ultimo_rolar = passo
                _trazida = ctx.pop("janela_trazida", "")
                if _trazida:
                    historico.append(
                        f"passo {passo}: ATENCAO — antes de escrever, a janela da frente "
                        f"tinha virado '{_trazida}' (o dono deve ter mexido). Eu trouxe de "
                        f"volta a janela da foto e so entao escrevi.")
                ultimos_movimentos.append(_assinatura_do_movimento(acao))
                # Deixa a tela reagir antes do proximo movimento do mesmo lote.
                # O BOTAO DIREITO PRECISA DOS 0,45 s (04/09/2026): o menu de
                # contexto tem animacao, e com 0,25 s a foto do passo seguinte
                # saia com o menu ainda nascendo — o Codex lia "nao mudou nada"
                # e clicava de novo, fechando o menu que ele mesmo abriu.
                _dormir(0.45 if tipo in ("clicar", "click", "clicar_duplo",
                                         "double_click", "clicar_direito", "right_click",
                                         "menu_direito", "clicar_nome", "clicar_texto",
                                         "tecla", "key") else 0.25, ctx)

            if terminou is not None and terminou_falhou and conferindo and ultima_conclusao:
                # "NAO CONSIGO CONFIRMAR" NA CONFERENCIA NAO E FALHA (23/09/2026).
                #
                # Caso do Diego: ele pediu para ATIVAR todas as notificacoes do
                # Defender, o JARVIS ligou tudo certo e disse "pronto". Na
                # conferencia a tela ja nao era a das notificacoes, ele nao
                # achou a lista e respondeu "falhou: nao consigo confirmar" — e
                # isso virou a ULTIMA frase da tarefa, por cima de um trabalho
                # certo. Nao conseguir OLHAR nao desfaz o que foi feito: a
                # primeira vez ele ganha o passo livre para abrir a tela da
                # prova; a segunda, a conclusao dele vale, com a ressalva.
                if not conferencia_falhou_uma_vez:
                    conferencia_falhou_uma_vez = True
                    historico.append(
                        f"CONFERENCIA: voce disse que nao consegue confirmar "
                        f"(\"{terminou[:140]}\"). Isso NAO e falha da tarefa. AGORA "
                        f"voce PODE agir para CHEGAR na tela onde a prova aparece "
                        f"(abrir_config, clicar no link ou menu que leva a ela, "
                        f"ler_tela) — sem mexer em nenhum interruptor. Quando a "
                        f"prova estiver na foto, responda concluido dizendo o que viu.")
                    log(f"[usar_computador] conferencia sem prova na tela; liberei para "
                        f"abrir a tela certa: {terminou[:80]}")
                    conferindo = False
                    so_comando_no_ultimo_passo = True
                    terminou = None
                    terminou_falhou = False
                    continue
                return (f"{ultima_conclusao.rstrip('.')}. So nao consegui conferir "
                        f"na tela no final.")
            if terminou is not None and not terminou_falhou and _parece_conclusao_boa(terminou):
                ultima_conclusao = terminou
            if terminou is not None:
                # ── A PROVA DE QUE ACABOU (22/09/2026, pedido do Diego) ─────
                #
                # "adicione um sistema de verificacao se esta certo, para ele
                #  nao entregar coisa errada e falar que terminou sem ter
                #  terminado"
                #
                # A conferencia de 07/09 ja existia, mas ele podia dizer
                # "concluido" na PRIMEIRA tela da conferencia, sem ter rolado
                # nada — e "tudo" continuava querendo dizer "o que cabia na
                # tela". Agora, para a conclusao valer:
                #
                #   - se o pedido fala em TODOS/TUDO/GERAL, ele tem de ter
                #     ROLADO durante a conferencia (senao nao viu a pagina
                #     inteira, e o que ele nao viu ele nao conferiu);
                #   - se a tarefa e de CONSERTO, ele tem de ter LIDO O ESTADO
                #     com um comando (`Get-Service`, `Test-NetConnection`...).
                #     Em conserto, foto de tela nao prova nada: o servico ou
                #     esta de pe ou nao esta.
                #
                # Sem a prova, a conclusao nao e recusada para sempre — ele
                # ganha UM passo a mais de conferencia com a cobranca exata do
                # que falta olhar. Isso custa segundos e vale a entrega certa.
                if _parece_conclusao_boa(terminou) and conferindo:
                    falta_prova = []
                    # CADA PROVA E COBRADA UMA VEZ SO (23/09/2026). A nota acima
                    # sempre disse "UM passo a mais", mas o codigo cobrava de
                    # novo a cada "concluido" — e quando a prova era impossivel
                    # (rolar uma tela que nao rola) ele ficava preso ate o
                    # contador de "tela parada" encerrar uma tarefa CERTA.
                    # Ele JA rolou esta pagina ha pouco (na hora de fazer)? Entao
                    # a prova existe: exigir de novo so fazia ele "continuar
                    # mexendo depois de terminar" (queixa do Diego, 23/09/2026).
                    if (_TAREFA_DIZ_TUDO.search(tarefa or "") and not conferencia_rolou
                            and passo - passo_do_ultimo_rolar > 6
                            and "rolar" not in provas_cobradas):
                        falta_prova.append(
                            "voce nao ROLOU a pagina nesta conferencia, e o "
                            "pedido fala em TODOS/TUDO. Role ate o fim, de "
                            "cinco em cinco, e confira cada grupo e cada item "
                            "da lista antes de dizer que acabou")
                    if (_TAREFA_DE_CONSERTO.search(tarefa or "") and not conferencia_provou
                            and "comando" not in provas_cobradas):
                        falta_prova.append(
                            "conserto se prova com COMANDO, nao com foto. Rode "
                            "um comando de leitura que mostre o problema "
                            "resolvido (Get-Service, Test-NetConnection, "
                            "Get-WinEvent, Get-PSDrive, Get-PnpDevice -Status "
                            "Error...) e me mostre a saida")
                    # MEXEU POR REGISTRO E NAO OLHOU A TELA (22/09/2026). Ver a
                    # regra G: o dono confere pelo interruptor, nao pela chave.
                    if mexeu_no_registro and not conferencia_rolou and "registro" not in provas_cobradas:
                        falta_prova.append(
                            "voce mexeu em REGISTRO/POLITICA nesta tarefa e nao "
                            "abriu a tela onde isso aparece. O dono confere pelo "
                            "interruptor, nao pela chave: abra a tela (Win+R com "
                            "o endereco ms-settings certo), OLHE como o item "
                            "esta, e me diga o que voce viu. Se a tela ainda "
                            "mostrar ligado porque a politica so vale depois de "
                            "reiniciar, diga isso ao dono em vez de dar por feito")
                    if falta_prova and conferencias < MAX_CONFERENCIAS:
                        conferencias += 1
                        for _f in falta_prova:
                            provas_cobradas.add("rolar" if "ROLOU" in _f else
                                                "comando" if "COMANDO" in _f else "registro")
                        so_comando_no_ultimo_passo = True     # conferir nao mexe na tela
                        historico.append(
                            "AINDA NAO ACEITO O 'CONCLUIDO': " + "; e ".join(falta_prova)
                            + ". Este passo continua sendo de CONFERENCIA (so "
                              "olhar e comando de leitura). Depois disso, "
                              "responda 'concluido' dizendo o que VIU.")
                        log(f"[usar_computador] conclusao sem prova: {falta_prova}")
                        conferindo = True
                        terminou = None
                        continue
                if (_parece_conclusao_boa(terminou) and conferir_agora
                        and conferencias < MAX_CONFERENCIAS):
                    conferencias += 1
                    conferir_agora = False
                    conferencia_rolou = False
                    conferencia_provou = False
                    historico.append(
                        f"VOCE DISSE QUE TERMINOU: \"{terminou[:120]}\".\n"
                        "ESTE PASSO E DE CONFERENCIA, E NELE VOCE SO OLHA. "
                        "NAO CLIQUE, NAO DIGITE, NAO TECLE — clicar de novo num "
                        "interruptor que voce ja mexeu DESFAZ o que estava "
                        "certo. Voce pode rolar a pagina, focar uma janela, ler o "
                        "texto da tela (ler_tela), ampliar e esperar; mais nada.\n"
                        "0) SE A JANELA DA TAREFA NAO ESTA NA FRENTE (outra janela "
                        "tapou, o dono mexeu), traga-a com focar_janela AGORA e "
                        "confira no passo seguinte — nao responda que 'falta "
                        "conferir'. EXCECAO: se a tarefa pedia para FECHAR aquela "
                        "janela, ela nao estar mais aberta E a prova — nao abra "
                        "de novo o que o dono mandou fechar.\n"
                        "0b) SE A TELA DA PROVA NAO ESTA ABERTA (a pagina mudou, "
                        "voltou para o inicio do programa), NAO responda 'falhou': "
                        "responda 'responder' dizendo em que tela a prova fica — "
                        "no passo seguinte voce fica livre para abrir essa tela e "
                        "olhar. Nao conseguir OLHAR nao quer dizer que a tarefa "
                        "falhou.\n"
                        f"OLHE a tela e confira, item por item, se a tarefa foi "
                        f"cumprida INTEIRA: \"{tarefa[:200]}\".\n"
                        "1) MUITA COISA TEM MAIS DE UM INTERRUPTOR. Numa pagina "
                        "de notificacoes, por exemplo, ha um grupo para virus, "
                        "outro para conta e outro para firewall — desligar so o "
                        "primeiro NAO cumpre o pedido. Role a pagina inteira, de "
                        "cinco em cinco, e confira todos.\n"
                        "2) Confira tambem se nada FORA do pedido foi alterado.\n"
                        "3) AGORA RESPONDA, sem mexer em nada:\n"
                        "   - se estiver tudo certo: acao 'concluido', dizendo o "
                        "que voce VIU (nao o que voce lembra de ter feito);\n"
                        "   - se faltou algo: acao 'responder', dizendo O QUE "
                        "falta e EM QUE TELA. No passo seguinte eu libero voce "
                        "para corrigir so isso.")
                    conferindo = True
                    provas_cobradas.clear()                   # rodada nova: cada prova, uma vez
                    so_comando_no_ultimo_passo = True         # conferir nao mexe na tela
                    log(f"[usar_computador] ele disse que terminou; mandei conferir "
                        f"({conferencias}/{MAX_CONFERENCIAS})")
                    if narrador:
                        narrador.andamento(passo, MAX_PASSOS)
                    terminou = None
                    continue
                return terminou

            # SO LEITURA, OU ELE MEXEU EM ALGUMA COISA? Ver `TETO_DE_BUSCAS`.
            _so_leu = bool(acoes) and all(
                _comando_so_le(a) and str(a.get("acao") or "").lower().strip() in (
                    "comando", "powershell", "cmd", "executar_comando", "rodar",
                    "esperar", "wait", "aguardar")
                for a in acoes)
            buscas_seguidas = (buscas_seguidas + 1) if _so_leu else 0

            # Este passo foi so de comando? Ver `so_comando_no_ultimo_passo`.
            _tipos = [str(a.get("acao") or "").lower().strip() for a in acoes]
            so_comando_no_ultimo_passo = bool(_tipos) and all(
                x in _NAO_MEXEM_NA_TELA for x in _tipos)

            if conferindo:
                # A CONFERENCIA ACABOU E ELE DISSE O QUE FALTA (23/09/2026).
                # No primeiro teste real ele ficou CINCO passos repetindo "falta
                # conferir, a Calculadora nao esta na frente" — ele nao sabia que
                # o passo seguinte ja estava liberado para agir. Agora sabe.
                historico.append(
                    "CONFERENCIA ENCERRADA: voce disse o que falta. AGORA voce PODE "
                    "agir de novo para resolver: se a janela da tarefa nao esta na "
                    "frente, traga-a com focar_janela (ou abrir_programa); se falta "
                    "fazer algo, faca. Depois responda concluido de novo, com a "
                    "prova na foto. Nao repita o recado: aja.")
            conferindo = False
            if mudou:
                # Mexeu na tela depois de uma conferencia: o que ele fizer agora
                # tambem precisa ser conferido antes de virar "terminei". Sem
                # isto, ou eu conferia so uma vez na tarefa toda, ou ficava
                # pedindo conferencia da mesma tela ate o teto — 18 s a toa.
                conferir_agora = True
            if len(ultimos_movimentos) >= 3 and len(set(ultimos_movimentos[-3:])) == 1 and not mudou:
                historico.append("AVISO: os tres ultimos movimentos foram iguais e a tela nao mudou. "
                                 "Tente um caminho diferente (outro elemento, atalho de teclado, "
                                 "ou abrir a pagina direto pela URL).")

        return ("Cheguei ao limite de passos sem terminar. Me diz como continuar "
                "que eu retomo de onde parei.")
    finally:
        freio_esc.parar()
        if narrador:
            narrador.parar()
        aviso(False)
        try:
            relay.encerrar_passos_na_tela(job_id)
        except Exception:
            pass
        log(f"[usar_computador] fim em {time.monotonic() - t0:.0f}s, {len(historico)} anotacoes")
        _registrar_passo(f"=== FIM em {time.monotonic() - t0:.0f}s")
