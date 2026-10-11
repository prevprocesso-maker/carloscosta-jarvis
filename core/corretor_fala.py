# -*- coding: utf-8 -*-
"""CONSERTO DA FALA MAL OUVIDA — 100% local, sem rede, sem modelo.

O problema (queixa do Diego, 05/09/2026): as vezes a voz dele chega no chat
com palavra pela metade ou trocada — "boa noite" virando "ba noit",
"Sulafat" virando "Osula Fati", "vision control" virando "VisioControl".
Nem sempre; as vezes.

O conserto tem TRES camadas, e so a primeira mora aqui:

  1. ESTE ARQUIVO — compara o que foi ouvido com um catalogo de expressoes que
     o JARVIS de fato usa, comparando pelo SOM e nao pela letra. Custo medido:
     menos de 1 ms por frase. Zero rede, zero modelo, zero espera.
  2. O CEREBRO — uma regra no prompt manda ele entender pelo contexto quando
     uma palavra nao fizer sentido (core/personas.py). Custo: zero, vai na
     mesma volta que ja acontecia.
  3. O `initial_prompt` do whisper (main.py `_VOCABULARIO`), que inclina o
     reconhecimento ANTES de ele errar.

POR QUE PELO SOM E NAO PELA LETRA: o whisper erra ouvindo, entao o erro dele
sempre PARECE com o certo quando falado. "Osula Fati" e "Sulafat" nao se
parecem escritos (difflib da 0,5) mas tem quase o mesmo som.

POR QUE EM GRUPO DE PALAVRAS E NAO PALAVRA POR PALAVRA: "ba" sozinho nao vira
"boa" com seguranca nenhuma — "ba" pode ser qualquer coisa. Mas "ba noit"
inteiro so pode ser "boa noite". O contexto e o que da a certeza, e por isso a
janela vai de cinco palavras ate uma, sempre da maior para a menor.

╔══════════════════════════════════════════════════════════════════════════╗
║ A REGRA QUE MANDA EM TUDO: e MUITO pior estragar uma frase certa do que   ║
║ deixar passar uma errada. O cerebro conserta o que escapa daqui; ninguem  ║
║ conserta o que este arquivo estragar.                                    ║
╚══════════════════════════════════════════════════════════════════════════╝

As tres travas que saem dessa regra, e que a primeira versao NAO tinha (o
teste `_teste_corretor_fala.py` reprovou 29 frases certas por causa disso):

  * NUMERO DE PALAVRAS. Uma expressao de duas palavras so troca uma janela de
    duas. Sem isso "Fecha o Bloco de Notas" virava "bloco de notas" e o verbo
    sumia; "Boa noite, meu querido" perdia o "meu".
  * PALAVRA COMUM DO PORTUGUES NAO E MEXIDA SOZINHA. E o que impede "porta" de
    virar "pasta" e "quase" de virar "Cazé".
  * JUNTAR OU SEPARAR PALAVRA so vale quando NENHUM pedaco e palavra de
    verdade. "Osula Fati" pode virar "Sulafat"; "a webcam" nunca perde o "a".
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

# ─────────────────────────────────────────────────────────────────────────────
# 1. O SOM DA PALAVRA
# ─────────────────────────────────────────────────────────────────────────────

_SO_LETRA = re.compile(r"[^a-z]")

# Ordem IMPORTA: digrafo primeiro, letra solta depois. Se "c" virar "k" antes
# de "ch" virar "x", "chave" vira "khave" e nada mais casa.
_TROCAS = [
    (re.compile(r"^h"), ""),                  # "hora" e "ora" soam igual
    (re.compile(r"ch"), "x"),
    (re.compile(r"lh"), "l"),
    (re.compile(r"nh"), "n"),
    (re.compile(r"ph"), "f"),
    (re.compile(r"sh"), "x"),
    (re.compile(r"th"), "t"),
    (re.compile(r"qu"), "k"),
    (re.compile(r"gu([ei])"), r"g\1"),
    (re.compile(r"sc([ei])"), r"s\1"),
    (re.compile(r"xc([ei])"), r"s\1"),
    (re.compile(r"c([ei])"), r"s\1"),
    (re.compile(r"g([ei])"), r"j\1"),
    (re.compile(r"c"), "k"),
    (re.compile(r"z"), "s"),
    (re.compile(r"y"), "i"),
    (re.compile(r"w"), "v"),
    (re.compile(r"x"), "s"),                  # "taxi"/"tassi", "exato"/"ezato"
    (re.compile(r"l($|[^aeiou])"), r"u\1"),   # brasileiro: "sal" soa "sau"
    (re.compile(r"m($|[^aeiou])"), r"n\1"),   # "bom"/"bon", "campo"/"canpo"
    (re.compile(r"ao$"), "au"),
    (re.compile(r"an$"), "au"),               # "falam" e "falao" pelo mesmo caminho
    # NASAL DO FIM SOME. Em portugues do Brasil o "m" final nao e consoante, e
    # nasalizacao: "bom" soa "bo~". E justamente a letra que o whisper come
    # (a queixa foi "boa noite" saindo "ba noit"). Sem esta linha "bo dia" e
    # "bom dia" ficam em 0,73 de semelhanca e o conserto nao acontece.
    (re.compile(r"n$"), ""),
    (re.compile(r"e$"), "i"),                 # "noite" soa "noiti"
    (re.compile(r"o$"), "u"),                 # "bloco" soa "blocu"
    # A MESMA COISA NO PLURAL. Sem estas duas, "Santos" (santos) e "Santus"
    # (santus) — que e como o whisper escreveu no log — davam 0,83 e ficavam
    # de fora por dois centesimos.
    (re.compile(r"os$"), "us"),               # "Santos" soa "Santus"
    (re.compile(r"es$"), "is"),               # "dentes" soa "dentis"
    (re.compile(r"(.)\1+"), r"\1"),           # letra dobrada nao muda o som
]


def som(texto: str) -> str:
    """A palavra escrita do jeito que ela SOA.

    O espaco some de proposito: o whisper erra a fronteira da palavra o tempo
    todo ("Osula Fati" contra "Sulafat", "web cam" contra "webcam"), e sem
    espaco os dois viram a mesma coisa.
    """
    s = str(texto or "").lower().replace("ç", "s")
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    # Roda palavra por palavra: as regras de FIM de palavra ("l" no fim, "e" no
    # fim) precisam saber onde a palavra acaba.
    saida = []
    for p in s.split():
        p = _SO_LETRA.sub("", p)
        if not p:
            continue
        for rx, novo in _TROCAS:
            p = rx.sub(novo, p)
        saida.append(p)
    return "".join(saida)


# ─────────────────────────────────────────────────────────────────────────────
# 2. O CATALOGO — o que o JARVIS de fato ouve todo dia
# ─────────────────────────────────────────────────────────────────────────────
#
# So entra aqui expressao que ELE usa: cumprimento, ordem, nome de programa,
# nome proprio. Verbo solto ("abre", "pesquisa") NAO entra: o whisper acerta
# esses, e como entrada de uma palavra so eles roubavam palavra parecida —
# "precisa" virou "pesquisa" no primeiro teste.
#
# Escreva a expressao como ela DEVE aparecer no chat, com acento e maiuscula.
# Nao precisa listar as formas erradas: o som se encarrega delas.
#
# CUIDADO AO ACRESCENTAR: se a expressao nova for parecida com outra que ele
# tambem diz, ponha AS DUAS. "tudo bem" e "tudo bom" soam quase igual — com as
# duas na lista cada uma casa consigo mesma e nenhuma vira a outra.

_FRASES = [
    # ── cumprimento e conversa de abertura ──────────────────────────────────
    "bom dia", "boa tarde", "boa noite", "boa madrugada",
    "tudo bem", "tudo bom", "tudo certo", "tudo joia",
    "como você está", "como é que você está",
    "com licença", "até logo", "boa sorte", "meu amigo", "meu querido",
    "bom dia JARVIS", "boa noite JARVIS", "boa tarde JARVIS",

    # ── NENHUMA ORDEM COM VERBO ENTRA AQUI ──────────────────────────────────
    #
    # Esta secao ja existiu ("ativar o computador", "criar uma imagem", "pode
    # continuar", "como vai"...) e foi arrancada depois de rodar o corretor em
    # cima das 175 falas reais do log do Diego. Ele reescreveu SEIS frases
    # certas, todas do mesmo jeito — trocando a forma do verbo por outra:
    #
    #     "Como assim usar o computador?"      -> "Como VAI usar o computador"
    #     "você pode CONTAR de 1 a 5?"         -> "pode CONTINUAR de 1 a 5"
    #     "DESCREVE um script em Python"       -> "ESCREVER um script"
    #     "não quero que você ATIVE o computador" -> "ATIVAR o computador"
    #
    # A ultima e a mais grave: "ativar computador" e a PORTA do computer use.
    # Reescrever a fala do dono para dentro da palavra que abre essa porta e
    # inaceitavel — ele disse exatamente o contrario.
    #
    # A licao: verbo o whisper acerta (e palavra curta e comum, dita todo dia);
    # quem ele erra e NOME — de programa, de pasta, de gente. O catalogo e de
    # nome e de expressao fixa. Verbo, nunca.
    "vision control",

    # ── coisas do Windows ───────────────────────────────────────────────────
    "bloco de notas", "gerenciador de tarefas", "gerenciador de arquivos",
    "explorador de arquivos", "painel de controle", "prompt de comando",
    "área de trabalho", "barra de tarefas", "menu iniciar",
    "pasta de download", "pasta de downloads", "minha pasta de download",
    "pasta de documentos", "pasta de imagens", "meus documentos",
    "disco rígido", "placa de vídeo", "placa de som", "fone de ouvido",
    "gerenciador de dispositivos", "configurações do Windows",
    "notificações do Windows", "Windows Defender", "central de segurança",

    # ── perguntas do dia a dia ──────────────────────────────────────────────
    "que horas são", "que dia é hoje", "data de hoje", "dia da semana",
    "previsão do tempo", "como está o tempo",
    "últimas notícias", "notícias do dia", "próximo jogo",

    # ── vocabulario de trabalho (so nome, nunca verbo) ──────────────────────
    "lista de empresas", "script em Python", "código em Python",
    "minha webcam", "Cazé TV",
]

# NOME PROPRIO E MARCA. Estes, e SO estes, podem ser consertados quando vem
# sozinhos numa palavra — e o unico jeito de "Solafat" voltar a ser "Sulafat".
#
# Fora da lista de proposito: Puck, Leda, Orus (vozes de nome curto demais —
# "leda" e "seda" sao a mesma coisa para o som) e qualquer nome com menos de
# cinco sons, como "Cazé", que roubaria "quase".
# OS NOMES DAS VOZES sao tratados a parte: por nao serem palavra de portugues
# nenhuma, eles podem ser comparados SO PELAS CONSOANTES, que e o unico jeito
# de alcancar o erro que o Diego viu ao vivo em 05/09/2026 — a mesma voz saiu
# "Sulifat", "Solifat" e "Sulifate" na mesma conversa. So a vogal muda, e vogal
# e justamente o que o reconhecimento erra mais.
#
# Isso NAO vale para nome que parece palavra ("Santos" e "cintos" tem o mesmo
# esqueleto), e por isso a lista e separada.
_NOMES_VOZ = [
    "JARVIS", "Sulafat", "Fenrir", "Algieba", "Umbriel", "Despina",
    "Achernar", "Vindemiatrix",
]

_NOMES = _NOMES_VOZ + [
    "WhatsApp", "YouTube", "Chrome", "Google", "Gmail", "Spotify",
    "Discord", "Telegram", "Instagram", "Facebook", "Netflix", "Twitch",
    "PowerPoint", "Photoshop", "Windows", "Notepad", "ChatGPT",
    "MuTron", "AlfaMu", "MuOnline", "BotZapBR", "Casimiro", "Kokoro",
    "OpenRouter", "webcam", "microfone",
    "Santos", "Corinthians", "Palmeiras", "Flamengo",
]

# ─────────────────────────────────────────────────────────────────────────────
# 3. PALAVRA COMUM — o escudo contra a correcao burra
# ─────────────────────────────────────────────────────────────────────────────
#
# Se a palavra ja e portugues de verdade, o whisper provavelmente acertou.
# Palavra comum SOZINHA nunca e trocada, e nunca some numa juncao: e assim que
# "porta" nao vira "pasta", "quase" nao vira "Cazé" e "a webcam" nao perde o
# "a". Em grupo ela entra sem medo, porque ai quem manda e a expressao inteira
# ("boa noite" e feita de duas palavras comuns).
#
# Esta lista e o escudo: quando aparecer uma correcao errada em cima de uma
# palavra legitima, o conserto e ACRESCENTAR A PALAVRA AQUI.

_COMUNS = set("""
a as o os um uma uns umas de do da dos das em no na nos nas por para pra pro
ao aos na nas pelo pela pelos pelas num numa dum duma
com sem sob sobre entre ate desde apos contra e ou mas porem entao porque
nem nao ne ta to pois assim tao contar contando conto contei
que quem qual quais quando onde como quanto quanta quantos quantas
eu tu ele ela nos vos eles elas me te se lhe lhes meu minha meus minhas
teu tua seu sua seus suas nosso nossa dele dela deles delas isso isto aquilo
esse essa este esta aquele aquela aqui ali la ai cá
sim nao talvez tambem so apenas ja ainda sempre nunca agora hoje ontem amanha
depois antes cedo tarde noite dia manha semana mes ano hora horas minuto
segundo hoje ontem
muito muita muitos muitas pouco pouca poucos poucas todo toda todos todas
nada tudo algo alguem ninguem outro outra outros outras mesmo mesma qualquer
maior menor mais menos melhor pior tao
ser estar ter haver fazer ir vir dar ver saber poder querer dizer falar
ficar passar chegar deixar achar levar trazer colocar pegar entrar sair
precisar gostar pensar sentir viver morar comprar vender pagar receber
sou es somos sao era eram foi fui fomos foram serei sera seremos serao seja
estou esta estamos estao estava estavam esteve estive estou tava
tenho tem temos tinha tinham teve tive tenha
faco faz fazemos fazem fazia fizeram fez fiz faca
vou vai vamos vao ia iam
dou damos dao dei deu
vejo ve vemos veem via viam vi viu veja
sei sabe sabemos sabem sabia sabiam soube
posso pode podemos podem podia podiam pude
quero quer queremos querem queria queriam quis
digo diz dizemos dizem dizia disse
falo fala falamos falam falava falei falou
fico fica ficamos ficam ficava fiquei ficou
preciso precisa precisamos precisam precisava precisei
gosto gosta acho acha achei achou penso pensa pensei
bom boa bons boas mau ma grande pequeno pequena novo nova velho velha
certo certa errado errada legal ruim otimo otima pessimo bonito bonita feio
alto alta baixo baixa rapido rapida lento lenta facil dificil forte fraco
cheio vazio limpo sujo quente frio longe perto inteiro pronto
coisa coisas gente pessoa pessoas homem mulher menino menina amigo amiga
casa carro trabalho dinheiro tempo vida mundo lugar parte partes lado forma
jeito modo caso vez vezes conta ponto fim comeco meio nome numero razao
porta portas janela janelas mesa cadeira parede chao teto rua cidade bairro
agua comida cafe almoco jantar loja lojas festa filme musica jogo time
mae pai filho filha irmao irmã familia amor deus santo santa
quase case cara velho po opa eita nossa caramba puxa entendi bem ta
ola oi tchau valeu obrigado obrigada favor licenca desculpa
abre abra abrir fecha feche fechar apaga apagar organiza organizar
pesquisa pesquisar procura procurar busca buscar mostra mostrar
analisa analise analisar digita digitar escreve escrever cria criar
manda mandar tira tirar poe coloca ativa ativar desativa desativar
desfaz desfazer para parar continua continuar espera esperar
computador celular teclado monitor camera internet tela mouse pasta arquivo
programa site video imagem foto texto musica noticia noticias
online offline link email whatsapp face insta
""".split())


# ─────────────────────────────────────────────────────────────────────────────
# 4. O INDICE — montado UMA vez, na abertura do programa
# ─────────────────────────────────────────────────────────────────────────────
#
# Sem indice a conta seria 300 entradas x 5 tamanhos de janela x 12 palavras =
# 18 mil comparacoes por frase (13,8 ms medidos na primeira versao — caro
# demais para uma promessa de "sem afetar a velocidade").
#
# O indice e de TRIGRAMA: cada entrada e fatiada em pedacos de tres sons e
# guardada por pedaco. Uma janela so e comparada com quem divide pelo menos
# dois pedacos com ela. "presisa" (precisa) e "peskisa" (pesquisa) dividem so
# um, entao nem chegam a ser comparados. Ficou em 0,4 ms.

def _monta_indice():
    entradas = []          # (chave, n_sons, sons_por_palavra, texto, e_nome)
    tri: dict[str, list] = {}
    for texto, e_nome in [(f, False) for f in _FRASES] + [(n, True) for n in _NOMES]:
        chave = som(texto)
        if not chave:
            continue
        # O som de CADA palavra da entrada, separado. E o que permite conferir,
        # na hora da troca, se a palavra comum do dono continua no lugar dela.
        item = (chave, len(chave), tuple(som(p) for p in texto.split()),
                texto, e_nome)
        idx = len(entradas)
        entradas.append(item)
        for g in _trigramas(chave):
            tri.setdefault(g, []).append(idx)
    return entradas, tri


def _trigramas(chave: str):
    if len(chave) < 3:
        return [chave]
    return [chave[i:i + 3] for i in range(len(chave) - 2)]


_ENTRADAS, _TRI = _monta_indice()
_MAIOR_JANELA = max(len(e[2]) for e in _ENTRADAS)


def _esqueleto(chave: str) -> str:
    """So as consoantes. E o que sobra de uma palavra quando as vogais erram."""
    return "".join(c for c in chave if c not in "aeiou")


# O esqueleto de cada nome de voz. So estes podem ser reconhecidos por
# consoante — ver o comentario em `_NOMES_VOZ`.
_ESQUELETOS_VOZ = {}
for _n in _NOMES_VOZ:
    _e = _esqueleto(som(_n))
    if len(_e) >= 3:
        _ESQUELETOS_VOZ.setdefault(_e, (som(_n), _n))

# PISOS DE SEMELHANCA. Medidos em cima dos erros reais do log do Diego e das
# frases certas que nao podem ser tocadas:
#
#     CONSERTA                                        NAO PODE
#     "ba noit"      x "boa noite"      0,857         "porta"      x "pasta"      0,60
#     "solafat"      x "sulafat"        0,857         "parte"      x "pasta"      0,60
#     "visiokontrou" x "visionkontrou"  0,96          "presisa"    x "peskisa"    0,77
#     "blokudinota"  x "blokudinotas"   0,956         "meu antigo" x "meu amigo"  0,82
#     "vatsap"       x "vhatsap"        0,923         "tudo bom"   x "tudo bem"   0,857*
#
#  * este ultimo so nao vira porque "tudo bom" TAMBEM esta no catalogo e casa
#    consigo mesmo com 1,00 — sempre ganha o mais parecido, nao o primeiro.
_PISO_FRASE = 0.85        # janela de duas palavras ou mais
_PISO_CURTO = 0.90        # quando um dos dois lados tem 5 sons ou menos
_PISO_NOME = 0.85         # nome proprio sozinho (so para os de _NOMES)
_PISO_JUNCAO = 0.85       # juntar ou separar palavra (ja protegida por palavra comum)
_MINIMO_SONS_SOZINHA = 5  # palavra sozinha com menos que isso nao e mexida

_PALAVRA = re.compile(r"[0-9A-Za-zÀ-ÿ]+")


def _candidatos(chave: str):
    """Quem no catalogo divide pelo menos dois trigramas com esta janela."""
    conta: dict[int, int] = {}
    for g in _trigramas(chave):
        for idx in _TRI.get(g, ()):
            conta[idx] = conta.get(idx, 0) + 1
    minimo = 1 if len(chave) <= 4 else 2
    return [_ENTRADAS[i] for i, c in conta.items() if c >= minimo]


def _melhor(chave: str, grupo: list, comuns: list, sons: list):
    """A entrada mais parecida que passa em TODAS as travas, ou nada."""
    tam = len(grupo)
    n = len(chave)
    escolha, melhor_r = None, 0.0
    for chave_cat, n_cat, sons_cat, texto_cat, e_nome in _candidatos(chave):
        dw = abs(len(sons_cat) - tam)
        if dw > 1:
            continue                      # numero de palavras tem de bater
        if dw == 0:
            if abs(n_cat - n) > 2 + n // 8:
                continue
            if tam == 1:
                # PALAVRA SOZINHA: so nome proprio, so se nao for palavra
                # comum do portugues e so se for comprida o bastante.
                if comuns[0] or n < _MINIMO_SONS_SOZINHA:
                    continue
                piso = _PISO_NOME if e_nome else 1.0    # 1.0 = so batida exata
            else:
                # PALAVRA COMUM NAO PODE SUMIR NEM MUDAR DE LUGAR.
                #
                # Duas frases certas foram estragadas por falta desta trava:
                # "desativar A VisioControl" perdeu o artigo para "desativar
                # vision control", e "ontem A TARDE" virou "ontem BOA tarde".
                # Nos dois casos o numero de palavras batia — o que nao batia
                # era a palavrinha comum, que tem de continuar onde estava.
                if any(c and not _mesmo_som(s, sc)
                       for c, s, sc in zip(comuns, sons, sons_cat)):
                    continue
                piso = _PISO_CURTO if min(n, n_cat) <= 5 else _PISO_FRASE
        else:
            # JUNTAR OU SEPARAR PALAVRA ("Osula Fati" -> "Sulafat"). So quando
            # NENHUM pedaco e palavra de verdade e nenhum tem uma letra so.
            if abs(n_cat - n) > 2 or any(comuns) \
                    or any(len(p) < 2 for p in grupo):
                continue
            # E CADA PEDACO TEM DE APARECER DO OUTRO LADO.
            #
            # Sem esta conferencia a juncao virava uma licenca para APAGAR a
            # palavra vizinha: no teste em cima dos registros, "Santos FC"
            # virou "Santos" e "webcam nem" virou "webcam". O pedaco que some
            # tem de estar contido no som do outro lado — "fati" esta dentro
            # de "sulafat", mas "fk" nao esta dentro de "santus".
            if len(sons_cat) < tam:
                if not _pedacos_cabem(sons, chave_cat):
                    continue
            elif not _pedacos_cabem(sons_cat, chave):
                continue
            piso = _PISO_JUNCAO
        r = 1.0 if chave == chave_cat else SequenceMatcher(None, chave, chave_cat).ratio()
        if r >= piso and r > melhor_r:
            escolha, melhor_r = texto_cat, r
    if escolha is None and tam == 1:
        escolha = _pelo_esqueleto(chave, comuns[0])
    return escolha


def _pelo_esqueleto(chave: str, comum: bool):
    """O NOME DA VOZ RECONHECIDO SO PELAS CONSOANTES.

    Na conversa do Diego de 05/09/2026 a MESMA voz saiu "Sulifat", "Solifat" e
    "Sulifate". Pela letra, "solifat" x "sulafat" da 0,857 — abaixo do piso — e
    os dois nem chegam a ser comparados, porque com duas vogais trocadas eles
    nao dividem trigrama nenhum. Pelas consoantes, os dois sao "slft".

    Isso e forte demais para uso geral ("cintos" e "Santos" tambem sao "snts"),
    entao vale SO para os nomes de voz, que nao sao palavra de portugues — e so
    quando o tamanho bate e a palavra ouvida nao e palavra comum.
    """
    if comum or len(chave) < _MINIMO_SONS_SOZINHA:
        return None
    achado = _ESQUELETOS_VOZ.get(_esqueleto(chave))
    if not achado:
        return None
    chave_nome, texto_nome = achado
    if chave == chave_nome or abs(len(chave_nome) - len(chave)) > 2:
        return None
    return texto_nome


def _pedacos_cabem(pedacos, alvo: str) -> bool:
    """Todo pedaco aparece, quase inteiro, dentro do som do outro lado?"""
    for p in pedacos:
        if not p:
            return False
        m = SequenceMatcher(None, p, alvo).find_longest_match(0, len(p), 0, len(alvo))
        if m.size < max(2, len(p) - 1):
            return False
    return True


def _mesmo_som(a: str, b: str) -> bool:
    """Os dois sao a mesma palavra falada? (aceita a ponta comida do whisper)"""
    if a == b:
        return True
    if not a or not b or abs(len(a) - len(b)) > 1:
        return False
    return SequenceMatcher(None, a, b).ratio() >= 0.80


def _e_comum(palavra: str) -> bool:
    """Palavra comum do portugues? O acento e tirado antes de olhar a lista —
    sem isso "à" nao era reconhecido como "a" e "ontem à tarde" virava
    "ontem boa tarde"."""
    s = unicodedata.normalize("NFD", str(palavra or "").lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn") in _COMUNS


def _mesma_capa(original: str, novo: str, comeco: bool) -> str:
    """Maiuscula so no COMECO da frase.

    Antes bastava a palavra ouvida vir com maiuscula — e "Desativar a
    VisioControl" saia "Desativar a Vision control", com maiuscula no meio da
    frase. O whisper poe maiuscula em qualquer palavra que ele acha nome
    proprio, entao ela nao serve de pista.
    """
    if comeco and original[:1].isupper() and novo[:1].islower():
        return novo[:1].upper() + novo[1:]
    return novo


def corrigir(texto: str) -> tuple[str, list]:
    """Devolve (texto_consertado, [(o_que_era, o_que_virou), ...]).

    Lista vazia quer dizer que nada foi tocado — o caso da grande maioria das
    frases. Quem chama registra a lista no log para poder conferir depois.
    """
    bruto = str(texto or "")
    achados = list(_PALAVRA.finditer(bruto))
    if not achados:
        return bruto, []

    palavras = [m.group(0) for m in achados]
    comuns = [_e_comum(p) for p in palavras]
    sons = [som(p) for p in palavras]
    total = len(palavras)
    # A JANELA NAO ATRAVESSA PONTUACAO. Entre uma palavra e a seguinte so pode
    # haver espaco; virgula, ponto ou quebra de linha fecham a janela ali. Sem
    # isto "Ba noit, JARVIS" era trocado inteiro e a virgula ia parar no fim da
    # frase ("Boa noite JARVIS,").
    colavel = [achados[k + 1].start() - achados[k].end() >= 0
               and not bruto[achados[k].end():achados[k + 1].start()].strip()
               for k in range(total - 1)]
    saida: list = list(palavras)
    trocas = []

    i = 0
    while i < total:
        andou = 0
        # Ate onde a janela pode ir sem atravessar pontuacao.
        limite = i
        while limite + 1 < total and colavel[limite]:
            limite += 1
        # DA MAIOR JANELA PARA A MENOR: "boa noite" tem de ser tentado antes de
        # "boa" sozinho, senao a metade da expressao ganha da expressao inteira.
        for tam in range(min(_MAIOR_JANELA, limite - i + 1), 0, -1):
            grupo = palavras[i:i + tam]
            chave = som(" ".join(grupo))
            if len(chave) < 3:
                continue
            # NUMERO NAO ENTRA EM JANELA. "194" nao tem som nenhum, entao ele
            # sumia da conta e a janela "194 microfone" casava com "microfone"
            # — apagando o numero. Apareceu 40 vezes no teste em cima dos
            # registros, e um numero apagado numa fala e erro grave.
            if any(not sons[i + k] for k in range(tam)):
                continue
            certo = _melhor(chave, grupo, comuns[i:i + tam], sons[i:i + tam])
            if certo is None:
                continue
            atual = " ".join(grupo)
            # Ja esta certo (ou so muda maiuscula): consome a janela e segue.
            if atual.lower() != certo.lower():
                antes = bruto[:achados[i].start()].rstrip()
                comeco = not antes or antes[-1] in ".!?:;"
                saida[i] = _mesma_capa(atual, certo, comeco)
                for k in range(1, tam):
                    saida[i + k] = None
                trocas.append((atual, certo))
            andou = tam
            break
        i += andou or 1

    if not trocas:
        return bruto, []

    # Remonta preservando tudo que nao e palavra (virgula, ponto, espaco).
    pedacos = []
    fim = 0
    for idx, m in enumerate(achados):
        if saida[idx] is None:
            # Palavra engolida por uma janela maior: o espaco que vinha antes
            # dela some junto (a janela nunca atravessa pontuacao, entao aqui
            # so ha espaco).
            fim = m.end()
            continue
        pedacos.append(bruto[fim:m.start()])
        pedacos.append(saida[idx])
        fim = m.end()
    pedacos.append(bruto[fim:])
    return "".join(pedacos), trocas
