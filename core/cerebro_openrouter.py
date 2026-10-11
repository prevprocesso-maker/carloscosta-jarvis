"""O CEREBRO DO JARVIS — hoje ele pensa pelo Codex da VPS, nao mais pelo OpenRouter.

MUDANCA DE 04/09/2026 (decisao do Diego, com os tres caminhos medidos):

    Gemini gratuita do cliente ..... 0,7 a 1,0 s   ~20 perguntas/dia, 429 direto
    OpenRouter gratuito do cliente . 1,1 a 1,9 s   50 pedidos/dia, as vezes vazio
    Codex CLI na nossa VPS ......... 4,5 a 5,4 s   SEM cota, no plano do dono

O OpenRouter saiu INTEIRO daqui — chave, modelos, fila de reserva. A escada
agora e: Gemini do cliente (quando a cota dela deixa) -> Codex da VPS (sempre).
O preco combinado sao os ~5 s por resposta; o ganho e que o JARVIS nao fica sem
responder no meio do dia de trabalho do cliente.

O NOME DO ARQUIVO E DA CLASSE FICOU. Nao e descuido: o que este arquivo faz
nunca foi "falar com o OpenRouter" — e orquestrar o prompt, a personalidade, o
historico, as travas anti-mentira e o corte do texto para a voz. Trocar o nome
mexeria em 30 pontos do main.py sem mudar uma linha de comportamento.

O computer use NAO passa por aqui (ordem do dono, e ele esta aprovado): ele fala
com o `jarvis-olhar.js` da VPS pelo seu proprio caminho.

Cerebro do JARVIS pelo OpenRouter — rapido e fora da cota da Gemini.

Medido em 30/08/2026 com a chave do Diego:
    openai/gpt-4.1-mini      1,1 s
    google/gemini-2.5-flash  1,3 s
    llama-3.3-70b            4,6 s
Contra 3 a 5 s da Gemini ao vivo (quando saudavel) e 12 a 19 s do Codex na VPS.

Com o whisper local (0,5 s) e o Kokoro local (1,0 s), a conversa inteira fica em
torno de 3 s **sem gastar um byte da cota de audio da Gemini**, que e cobrada por
SEGUNDO e era o que estrangulava tudo.

Conversa fica aqui; TRABALHO vai para o Codex na VPS. O proprio modelo decide,
por chamada de ferramenta — nada de adivinhar por palavra-chave, que erra tanto
para mais ("que horas sao" virando pesquisa) quanto para menos.

Ferramentas ligadas (30/08/2026, a pedido do Diego):
    hora_agora     respondida AQUI mesmo, do relogio do PC: instantanea
    pesquisar      noticias e qualquer coisa que precise da internet -> Codex
    criar_imagem   -> Codex (kind image)
    editar_imagem  -> Codex (kind image_edit), usa a foto arrastada na janela
    buscar_leads   -> Codex (kind leads)
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

# Verbos que indicam PEDIDO DE ACAO. Se o cliente usou um destes e o modelo nao
# chamou ferramenta, ele esta inventando que fez.
import re as _re_acao

_VERBOS_DE_ACAO = (
    "abre", "abrir", "abra", "fecha", "fechar", "feche", "encerra", "encerrar",
    "salva", "salvar", "salve", "cria", "criar", "crie", "apaga", "apagar",
    "apague", "exclui", "excluir", "exclua", "deleta", "deletar", "manda",
    "mandar", "mande", "envia", "enviar", "envie", "toca", "tocar", "toque",
    "procura", "procurar", "busca", "buscar", "pesquisa", "pesquisar",
    # Estes faltavam e custaram dois casos na bateria de 30/08: "lista as pastas
    # do disco D" e "mostra os cinco arquivos mais recentes" nao chamavam
    # ferramenta nenhuma — o modelo respondia de cabeca sobre o computador do
    # dono, que ele nao conhece. Verbo de CONSULTA tambem e acao.
    "lista", "listar", "liste", "mostra", "mostrar", "mostre", "conta",
    "contar", "conte", "ve", "ver", "veja", "verifica", "verificar",
    "verifique", "compacta", "compactar", "zipa", "zipar", "descompacta",
    "descompactar", "extrai", "extrair",
    "renomeia", "renomear", "move", "mover", "copia", "copiar", "escreve",
    "escrever", "escreva", "liga", "ligar", "desliga", "desligar", "baixa",
    "baixar", "instala", "instalar", "sai do", "saia do", "minimiza",
)


# ASSUNTOS QUE SO A MAQUINA SABE (bateria de 30/08/2026).
#
# "Quanto tempo meu computador esta ligado" nao tem verbo de acao nenhum — e
# mesmo assim a resposta so pode vir de dentro do PC. Sem esta lista o modelo
# respondia de cabeca, inventando numero sobre uma maquina que ele nunca viu.
# Verbo denuncia a acao; ASSUNTO denuncia a consulta.
_ASSUNTOS_DA_MAQUINA = (
    "disco c", "disco d", "disco e", "hd", "ssd", "espaco livre", "espaco em",
    "memoria ram", " ram ", "cpu", "processador", "processos", "meu ip",
    "ip na rede", "endereco ip", "rede wifi", "wi-fi", "windows", "meu pc",
    "meu computador", "esta ligado", "ligado ha", "placa de video", "driver",
    "antivirus", "temperatura", "resolucao da tela", "area de trabalho",
    "meus arquivos", "minhas pastas", "programas instalados", "bateria do",
)


# "Conta" tem dois sentidos e so um e tarefa: "conta quantos arquivos tem na
# pasta" e consulta ao PC, mas "me conta uma piada" e conversa. Sem esta
# excecao a trava cobrava ferramenta para contar piada e o JARVIS respondia
# que nao consegue.
_CONVERSA_DISFARCADA = _re_acao.compile(
    r"\b(conta|conte|contar|fala|fale|falar|diz|diga|manda|mande|mandar|"
    r"da|de|solta|solte|canta|cante|cantar|explica|explique|explicar)\b"
    r"[^.?!]{0,24}"
    r"\b(piada|historia|estoria|curiosidade|caso|novidade|segredo|fofoca|"
    r"sobre|de novo|outra|salve|alo|abraco|beijo|tchau|oi|forca|real|ver|"
    r"bronca|papo|letra|frase|verso|musica|parabens|recado|motivacao|"
    r"resenha|zoeira|indireta)\b")

# Pedido de FALA: cumpre-se falando, nao chamando ferramenta. "Manda um salve
# pra rapaziadinha" nao e tarefa de computador — e o JARVIS abrindo a boca.
# Em 30/08/2026 a trava tratou isso como acao, nao achou ferramenta e obrigou a
# recusa: "nao tenho ferramenta pra mandar audio ou mensagem". O dono odiou, com
# razao. Quando o alvo do pedido e uma PESSOA ou um PUBLICO, e conversa.
_PEDIDO_DE_FALA = _re_acao.compile(
    r"\b(manda|mande|mandar|da|de|dar|solta|solte|fala|fale|falar|diz|diga|"
    r"dizer|canta|cante|cantar|conta|conte)\b[^.?!]{0,30}"
    r"\b(pra|para|pro|a)\b\s+"
    r"(galera|rapaziada|rapaziadinha|pessoal|turma|povo|gente|molecada|"
    r"meninada|publico|inscritos|seguidores|pessoal ai|todo mundo|eles|elas|"
    r"mim|nos|voces)\b")


# PROMESSA DE FUTURO — a mentira que a trava original nao pegava.
#
# Em 31/08/2026 o Diego pediu CPU, RAM e disco. O JARVIS chamou a ferramenta,
# falou do processador e terminou com "vou abrir a aba de Performance, ja
# volto". Nao existe "ja volto": o turno acabou ali e ele nunca voltou.
#
# Nao basta ter chamado ferramenta; a resposta tambem nao pode empurrar o
# trabalho para um futuro que nao vai existir.
_PROMESSA_DE_FUTURO = _re_acao.compile(
    r"\b(ja volto|ja retorno|volto ja|volto em seguida|ja ja te|"
    r"vou (abrir|ver|verificar|checar|olhar|buscar|procurar|puxar|conferir)|"
    r"deixa eu (ver|olhar|abrir|verificar|conferir)|"
    r"um instante|so um momento|so um minuto|aguarda(e|ndo)? (um|ai)|"
    r"em seguida eu|depois eu (vejo|olho|abro|te falo)|te (falo|aviso) daqui)\b")


def _promete_sem_cumprir(texto: str) -> bool:
    import unicodedata as _u
    t = _u.normalize("NFD", str(texto or "").lower())
    t = "".join(c for c in t if _u.category(c) != "Mn")
    return bool(_PROMESSA_DE_FUTURO.search(t))


# PERGUNTA QUE SO O MUNDO RESPONDE (01/09/2026).
#
# Hora, data, noticia, cotacao, clima: nada disso esta na cabeca do modelo, e o
# que ele "sabe" esta velho por meses. Em 01/09/2026, a uma da manha, o Diego
# perguntou a hora e ouviu "quatro e cinco da tarde" — inventado.
#
# A regra ja estava escrita no prompt e ele quebrou assim mesmo. Aqui vira
# trava: sem ferramenta chamada, a resposta e cobrada.
_PRECISA_DO_MUNDO = (
    "que horas", "horas sao", "horas e", "que hora", "a hora", "das horas",
    # "que dia" sozinho e generico demais: pegava "nossa, que dia dificil foi
    # esse" e o JARVIS respondia a data. As formas abaixo so aparecem quando a
    # pessoa quer MESMO saber o dia.
    "dia e hoje", "dia de hoje", "data de hoje", "hoje e dia", "que dia estamos",
    "que dia sera", "que mes", "que ano",
    "dia da semana", "amanha e", "ontem foi",
    "noticia", "noticias", "manchete", "aconteceu hoje", "ultimas do",
    "cotacao", "dolar", "euro", "bitcoin", "bolsa hoje",
    "clima", "previsao do tempo", "vai chover", "temperatura hoje",
    "resultado do jogo", "quem ganhou", "placar",
)


# CASAR POR PALAVRA INTEIRA, nao por pedaco.
#
# Em 01/09/2026 o Diego disse "amanha EU tenho que trabalhar" e o JARVIS
# respondeu a hora — tres vezes. O termo "amanha e" da lista casava dentro de
# "amanha eu": busca por substring nao sabe onde a palavra acaba. Com fronteira
# de palavra o problema some, e as perguntas de verdade continuam pegando.
_RE_MUNDO = _re_acao.compile(
    r"\b(?:" + "|".join(a.replace(" ", r"\s+") for a in _PRECISA_DO_MUNDO) + r")\b")


# HORA COM NUMERO NA FRENTE E DURACAO, NAO PERGUNTA (01/09/2026).
#
# "tenho que trabalhar daqui a 4 HORAS E nao to a fim de dormir" disparou o
# gatilho "horas e" e o JARVIS respondeu que horas eram — pela terceira vez na
# mesma conversa. As palavras do gatilho estavam la, mas ninguem tinha
# perguntado nada.
#
# A regra que separa os dois casos e simples e nao tem excecao conhecida:
# quando vem quantidade antes ("4 horas", "duas horas", "meia hora", "24
# horas"), e prazo. Pergunta de hora nao tem numero. Entao esses trechos somem
# do texto antes da busca, e o resto da frase continua sendo lido normalmente.
_RE_DURACAO = _re_acao.compile(
    r"\b(?:\d+|um|uma|dois|duas|tres|quatro|cinco|seis|sete|oito|nove|dez|onze|"
    r"doze|meia|poucas?|algumas?|varias?|muitas?|quantas)\s*(?:e\s+meia\s+)?"
    r"horas?\b")


def _precisa_do_mundo(texto: str) -> bool:
    import unicodedata as _u
    t = _u.normalize("NFD", str(texto or "").lower())
    t = "".join(c for c in t if _u.category(c) != "Mn")
    t = _RE_DURACAO.sub(" ", t)
    return bool(_RE_MUNDO.search(t))


def _parece_pedido_de_acao(texto: str) -> bool:
    import unicodedata as _u
    t = _u.normalize("NFD", str(texto or "").lower())
    t = "".join(c for c in t if _u.category(c) != "Mn")
    # Mesma limpeza do `_precisa_do_mundo`: "daqui a 4 horas" e prazo.
    t = _RE_DURACAO.sub(" ", t)
    # Hora, noticia e companhia vem ANTES de qualquer excecao: nem o pedido de
    # fala salva ("me fala que horas sao" continua exigindo o relogio).
    if _precisa_do_mundo(t):
        return True
    if _PEDIDO_DE_FALA.search(t):
        return False
    if _CONVERSA_DISFARCADA.search(t) and not any(
            a in f" {t} " for a in _ASSUNTOS_DA_MAQUINA):
        return False
    if any(f" {v} " in f" {t} " or t.startswith(v + " ") for v in _VERBOS_DE_ACAO):
        return True
    return any(a in f" {t} " for a in _ASSUNTOS_DA_MAQUINA)


def _resumir(texto: str, teto: int) -> str:
    """Corta a descricao no fim de uma frase, sem passar do teto."""
    texto = " ".join(str(texto or "").split())
    if len(texto) <= teto:
        return texto
    import re as _re
    frases = _re.split(r"(?<=[.!?]) ", texto)
    saida = ""
    for f in frases:
        if saida and len(saida) + len(f) + 1 > teto:
            break
        saida = (saida + " " + f).strip()
    return saida or texto[:teto]


# NAO HA MAIS FILA DE MODELOS GRATUITOS (04/09/2026).
#
# Ela existia porque cada modelo `:free` do OpenRouter tinha seu proprio teto e
# um so nao aguentava o dia. Com o cerebro no Codex da VPS o problema deixou de
# existir: nao ha cota, nao ha 429, nao ha modelo cansado. O que sobrou desta
# ideia esta no `jarvis-cerebro.js` da VPS, que renova a thread a cada 10
# perguntas — e por contexto, nao por limite.
MODELO_PADRAO = "codex"

# Quantas mensagens da conversa entram em cada pedido.
#
# ERA 6 — tres idas e voltas — e isso quebrava a conversa na cara do dono: em
# 01/09/2026 ele pediu para achar uma palavra na tela, ela achou e explicou, ele
# disse "nao estou encontrando" e ela perguntou "voce esta vendo essa tela no
# celular ou no computador?". Tinha esquecido duas frases antes.
#
# O aperto existia por causa do teto de ~7.866 fichas por pedido da conta gratis
# do OpenRouter. Esse teto nao manda mais aqui: o cerebro hoje e o Luna ou a
# Gemini flash-lite, ambos com contexto enorme.
#
# 40 mensagens = 20 trocas, uma conversa inteira. Custa umas 800 fichas de
# entrada por pedido, a $0,20 o milhao e com boa parte batendo no cache a $0,02
# — fracao de centavo. Esquecer o assunto sai muito mais caro que isso.
# 120 mensagens = 60 trocas (01/09/2026). O Diego pediu que ele lembre a
# conversa INTEIRA enquanto o programa estiver aberto — meia hora falando
# passa facil de 20 trocas. Zera quando o programa fecha, como ele quer.
MEMORIA_MAX = 120

# O OpenRouter gratuito, quando entra como ultimo recurso, continua no aperto:
# la o teto por pedido e real e derruba a resposta inteira.
MEMORIA_GRATUITO = 6


# O RELOGIO VAI NA CONVERSA, NAO NO PROMPT DE SISTEMA (03/09/2026).
#
# Por que existe: "que horas sao" era a pergunta mais comum do cliente e a mais
# lenta do programa. Ela obrigava DUAS voltas no modelo — uma para ele pedir o
# `hora_agora`, outra para ele falar o resultado — e cada volta custa ~2 s.
# Medido: 5,75 s com as duas chamadas contra 2,33 s com o relogio ja na mao, e
# as cinco perguntas de teste responderam certo.
#
# Por que na ENTRADA e nao no prompt de sistema, que seria o lugar obvio:
#
# 1. O CACHE. O prefixo (prompt + 36 ferramentas, ~6.900 fichas) bate 99% no
#    cache da OpenAI. Um relogio dentro do prompt de sistema muda o PRIMEIRO
#    item a cada minuto e invalida o prefixo inteiro — pagaria em prefill o que
#    economizou em chamada. Depois do prompt, o prefixo continua igual: medido,
#    o cache ficou em 98-99%.
# 2. O RELOGIO CONGELADO. Ja aconteceu neste programa: dado que envelhece
#    dentro do prompt fica velho porque o prompt e montado uma vez. Aqui a
#    linha e refeita a cada `perguntar()`, junto com o resto das mensagens.
#
# A LINHA TEM DE ESTAR SEMPRE LA. Sem ela o modelo volta a responder de cabeca:
# no teste, sem a linha e sem a trava antiga, ele disse "estamos em marco" com
# toda a confianca. O prompt manda chamar `hora_agora` quando a linha faltar
# (ver a REGRA ABSOLUTA DO RELOGIO em core/personas.py), mas ele obedeceu isso
# em 1 de 2 — entao quem garante e este codigo, que sempre injeta.
_DIAS_DA_SEMANA = ("segunda-feira", "terca-feira", "quarta-feira",
                   "quinta-feira", "sexta-feira", "sabado", "domingo")
_MESES = ("janeiro", "fevereiro", "marco", "abril", "maio", "junho", "julho",
          "agosto", "setembro", "outubro", "novembro", "dezembro")


# PEDIDO COM MAIS DE UMA ACAO (05/09/2026).
#
# Caso real: "abre o meu bloco de notas E DENTRO DELA DIGITA um script em Python
# de uma calculadora". O JARVIS chamou `open_app`, recebeu "o Bloco de Notas ja
# esta aberto" e PAROU ali — tratou o primeiro passo como se fosse a tarefa
# inteira. O laco de varias voltas existe no `cerebro_codex`; o que faltava era
# alguem AVISAR que ainda havia trabalho.
#
# A marca e simples: dois verbos de acao ligados por "e", "depois" ou "entao".
# Conservador de proposito — na duvida nao marca, e o comportamento e o de
# sempre.
_VERBO_DE_TAREFA = (r"abr[ea]|abrir|fech[ae]|fechar|digit[ae]|digitar|escrev[ae]|"
                    r"escrever|descrev[ae]|descrever|redi[jg][ae]|redigir|cod[ae]|codar|"
                    r"salv[ae]|salvar|grav[ae]|gravar|cri[ae]|criar|faz|fazer|"
                    r"manda|mandar|envi[ae]|enviar|pesquis[ae]|pesquisar|"
                    r"busc[ae]|buscar|toc[ae]|tocar|baix[ae]|baixar|"
                    r"renomei[ae]|renomear|copi[ae]|copiar|mov[ae]|mover|"
                    r"apag[ae]|apagar|organiz[ae]|organizar|po[eõ]|coloc[ae]|colocar")
_PEDIDO_ENCADEADO = _re_acao.compile(
    r"\b(" + _VERBO_DE_TAREFA + r")\b.{0,80}?"
    r"\b(e|entao|depois|e depois|e ja|e tambem|e ai|e la)\b.{0,40}?"
    r"\b(" + _VERBO_DE_TAREFA + r")\b", _re_acao.I)


def _linha_do_relogio() -> str:
    """A hora certa, escrita como AVISO — nunca como pergunta.

    A primeira versao dizia so "RELOGIO DA MAQUINA, agora: ..." e ia como
    uma mensagem SOLTA do dono. O modelo leu aquilo como assunto posto na
    mesa e passou a responder a ele: TODA fala terminava com "Que horas
    sao? Sabado, cinco de setembro de dois mil e vinte e seis, tres da
    manha" — sem ninguem ter perguntado (05/09/2026, o Diego viu em todas).

    Duas coisas consertam: a ordem explicita de NAO falar a hora sem ser
    perguntado (aqui) e o fim do turno solto (ver `perguntar`).
    """
    import time as _t  # noqa: PLC0415
    a = _t.localtime()
    return ("Relogio da maquina neste instante: %s, %d de %s de %d, %02d:%02d. "
            "E a hora de verdade do computador do dono. NAO fale a hora nem a "
            "data se ele nao estiver perguntando por elas; quando ele perguntar, "
            "responda com o que esta escrito aqui, sem chamar ferramenta e "
            "dizendo SO a hora — nunca cite esta nota nem 'o sistema'."
            % (_DIAS_DA_SEMANA[a.tm_wday], a.tm_mday, _MESES[a.tm_mon - 1],
               a.tm_year, a.tm_hour, a.tm_min))


def _quanto_calar_a_gemini(texto_do_erro: str) -> tuple[float, str]:
    """Um 429 da Gemini NAO quer dizer, sozinho, que o dia acabou (04/09/2026).

    O defeito: qualquer 429 calava a Gemini por 24 HORAS. So que a conta
    gratuita tem DOIS limites — por MINUTO e por DIA — e os dois chegam com o
    mesmo codigo 429. Fazer tres perguntas seguidas (exatamente o que acontece
    num teste ao vivo, ou quando o dono conversa animado) estoura o de minuto...
    e o JARVIS passava o resto do DIA no degrau lento, respondendo em 2 a 16 s em
    vez de 1 s. Provado aqui: o 429 chegou e, quinze segundos depois, a mesma
    chave respondeu normalmente.

    Agora o silencio e do tamanho do problema:
      - o erro fala em dia/diario  -> 24 h (a cota do dia acabou mesmo);
      - o erro traz `retryDelay`   -> exatamente o que o Google pediu;
      - qualquer outro 429         -> 45 s, que e o limite por minuto passando.
    """
    import re as _re  # noqa: PLC0415
    t = (texto_do_erro or "").lower()
    # O PRAZO QUE O PROPRIO GOOGLE MANDA VEM PRIMEIRO (04/09/2026).
    #
    # A mensagem real desta conta traz as duas coisas ao mesmo tempo:
    #   "Please retry in 34.8s"  ...  quotaId: GenerateRequestsPerDayPerProject
    # Lendo o ROTULO ("PerDay") eu calaria a Gemini por 24 horas quando ela
    # volta em 35 segundos — e foi assim que o JARVIS passou a noite inteira no
    # degrau lento do OpenRouter (2 a 16 s por resposta) em vez de responder em
    # 1 s. O prazo e informacao; o rotulo e so nome de contador.
    achado = _re.search(r"retry(?:delay)?['\"]?\s*(?:in|:|=)?\s*['\"]?(\d+)", t)
    if achado:
        return (max(5.0, min(300.0, float(achado.group(1)))),
                "o proprio Google pediu para esperar")
    if ("perday" in t or "per day" in t or "daily" in t or "per_day" in t
            or "requests per day" in t):
        return 24 * 3600, "cota do DIA"
    return 45.0, "limite por MINUTO"



def _openai_morta(texto_do_erro: str) -> bool:
    """A conta da OpenAI nao vai responder nesta abertura do programa? (01/10/2026)

    Chave ausente (o PC do cliente nunca tem), chave recusada ou conta SEM
    CREDITO — foi o que aconteceu em 30/09/2026, e cada comando passou a pagar
    uma ida a rede so para ouvir "You have no credits remaining" antes de cair no
    degrau seguinte. Nenhuma dessas passa sozinha em minutos: desliga o degrau
    ate a proxima abertura. Limite por minuto (429 sem falar em credito) NAO
    entra: esse passa.
    """
    t = (texto_do_erro or "").lower()
    return any(x in t for x in ("ausente", "api key", "credit", "insufficient_quota",
                                "billing", "credit_balance", "invalid_api_key"))


class CerebroOpenRouter:
    def __init__(self, modelo: str = MODELO_PADRAO, sistema: str = "") -> None:
        # NAO LE MAIS CHAVE NENHUMA AQUI (04/09/2026). A do OpenRouter deixou
        # de existir no caminho do cerebro, e a da Gemini quem le e o
        # `cerebro_gemini`. Sem isto, o JARVIS nem abria quando faltava a chave —
        # e agora ele PRECISA abrir mesmo sem chave nenhuma do cliente, porque
        # o Codex da VPS responde de qualquer jeito.
        self.modelo = modelo or MODELO_PADRAO
        # A ponte com a VPS (o `relay_client`). Quem injeta e o main.py, que e
        # quem a tem. Sem ela sobra a Gemini; sem as duas, ele avisa o dono em
        # vez de inventar resposta.
        self.ponte = None
        self._codex = None
        # A conversa A PARTE, para os pedidos que nascem DENTRO de outro
        # pedido (escrever o texto que a ferramenta vai digitar).
        self._codex_aux = None
        # Voz opcional: quando o main a injeta, os passos do meio de uma tarefa
        # encadeada sao falados na hora, em vez de o dono esperar calado.
        self.falar = None
        # O jeito de ser vem de core/personas.py, escolhido pelo cliente na
        # engrenagem. `sistema` explicito continua vencendo, para teste.
        self.personalidade = "equilibrado"
        self.sistema = sistema or self._prompt_da_personalidade()
        self.historico: list[dict] = []
        # Quem executa de fato. O main.py injeta; assim este arquivo nao precisa
        # conhecer o relay, a janela nem o disco.
        self.executor = None
        # Preenchida pelo main.py com as 28 que ja existem, convertidas.
        self.ferramentas: list = []
        # 0 = todas; 1 = so as essenciais; 2 = as seis principais; 3 = nenhuma.
        # Sobe sozinho quando o OpenRouter recusa por teto de fichas.
        #
        # COMECA EM 1 de proposito. Medido em 30/08: as 28 ferramentas dao ~6.328
        # fichas com a instrucao de sistema, e o teto da conta gratis caiu para
        # 5.900 no mesmo dia. O nivel 0 falharia SEMPRE, e cada falha custa uma
        # ida e volta inutil antes de o cliente ouvir qualquer coisa.
        # Nivel 1 = 12 ferramentas = ~2.674 fichas, com folga larga.
        # Volta a 0 (todas as 28): os modelos gratuitos tem contexto de 250 mil
        # a 1 milhao de fichas, entao o teto que nos atormentou nao existe neles.
        # Se algum apertar, o proprio laco corta sozinho e tenta de novo.
        # NIVEL 1 fixo: so as tres essenciais. Nao e economia de fichas — e
        # precisao. Menos opcao parecida, menos escolha errada.
        self._nivel_corte = 1

    @staticmethod
    def converter_ferramentas(declaracoes: list) -> list:
        """Traduz as declaracoes do formato Gemini para o formato OpenAI.

        Diferencas que importam: a Gemini usa "OBJECT"/"STRING" em MAIUSCULA e
        entrega a declaracao crua; o OpenRouter espera minuscula e um embrulho
        {"type": "function", "function": {...}}.
        """
        def desce(no):
            if isinstance(no, dict):
                saida = {}
                for k, v in no.items():
                    saida[k] = v.lower() if (k == "type" and isinstance(v, str)) else desce(v)
                return saida
            if isinstance(no, list):
                return [desce(x) for x in no]
            return no

        convertidas = []
        for d in declaracoes or []:
            nome = d.get("name")
            if not nome:
                continue
            params = desce(d.get("parameters") or {})
            if not isinstance(params, dict):
                params = {}
            params.setdefault("type", "object")
            params.setdefault("properties", {})
            convertidas.append({
                "type": "function",
                "function": {
                    "name": nome,
                    # DESCRICAO ENXUTA. As do formato Gemini trazem paragrafos
                    # de explicacao, e somadas dao ~7.819 fichas nas 28 — mais
                    # que o teto de 7.866 fichas por pedido da conta gratis do
                    # OpenRouter. Resultado: erro 402 e o JARVIS mudo.
                    #
                    # Cortar na FRASE, nao no caractere: uma descricao truncada
                    # no meio da palavra confunde o modelo mais do que ajuda.
                    "description": _resumir(d.get("description") or "", 110),
                    "parameters": params,
                },
            })
        return convertidas

    def escrever_comando(self, tarefa: str, timeout: float = 25.0) -> str:
        """UM comando de PowerShell para a tarefa, pelo Codex da VPS.

        Este era o ultimo degrau da antiga fila do OpenRouter. Com ela fora, o
        degrau final passou a ser o Codex — que, para escrever comando, e o
        melhor dos dois: e o mesmo agente que ja escreve os passos do computer
        use, e comando torto aqui vira erro no PowerShell do cliente.
        """
        from actions.tarefa_comando import PEDIDO_AO_CODEX, SISTEMA_DO_COMANDO  # noqa: PLC0415
        mensagens = [
            {"role": "system", "content": SISTEMA_DO_COMANDO},
            {"role": "user", "content": PEDIDO_AO_CODEX + str(tarefa or "")},
        ]
        cerebro = self._cerebro_codex(a_parte=True)
        if cerebro is None:
            raise RuntimeError("sem Gemini e sem ponte com a VPS para escrever o comando")
        saida = cerebro.escrever(
            SISTEMA_DO_COMANDO, str(mensagens[-1]["content"])).strip()
        if not saida:
            raise RuntimeError("o Codex nao escreveu o comando")
        # Ele as vezes devolve o comando dentro de cerca de codigo, mesmo com a
        # instrucao acima. Tirar aqui e mais barato do que mais uma volta.
        import re as _re  # noqa: PLC0415
        saida = _re.sub(r"^```[a-z]*\s*|\s*```$", "", saida.strip()).strip()
        return saida.splitlines()[0].strip() if saida else saida

    def _persona_para_o_codex(self) -> str:
        """O JEITO INTEIRO — para o cerebro que NAO recebe o prompt de sistema.

        POR QUE ISTO EXISTE (05/09/2026). Quando o cerebro passou a ser o Codex,
        o Diego notou na hora: "antes ele seguia o jeito da voz, agora ele fala
        menos". Estava certo, e a causa era estrutural:

            caminho da Gemini ... recebe `self.sistema` inteiro (~8.000 letras):
                                  quem ele e, o dono, as regras e o jeito todo.
            caminho do Codex .... recebia SO o `contexto.jeito`, que era o
                                  resumo de tres linhas do `_lembrete_do_jeito`
                                  — e a VPS ainda cortava em 400 letras.

        Com um terco da personalidade na mao, ele respondia curto e sem sal.
        Aqui vai o bloco COMPLETO do jeito escolhido, mais o nome e as regras de
        fala. O teto da VPS subiu de 400 para 2.000 no mesmo dia; 1.800 aqui
        deixa folga para o corte nunca acontecer no meio de uma frase.
        """
        try:
            from core.personas import PERSONALIDADES, nome_da_ia  # noqa: PLC0415
            p = PERSONALIDADES.get(self.personalidade)
            if not p:
                return ""
            nome = nome_da_ia(getattr(self, "personagem", "")) or "JARVIS"
            partes = [
                f"Voce e {nome}, o assistente pessoal do dono deste computador.",
                str(p.get("jeito") or "").strip(),
                # As mesmas regras de fala que o prompt grande carrega: sem
                # elas ele escreve para ser LIDO, e o que sai daqui e FALADO.
                "COMO ESCREVER: isto vai ser dito em voz alta. Nada de emoji, "
                "asterisco, lista ou titulo. Frases curtas, do jeito que se "
                "fala. Cabe um 'hmm' quando voce pensa, um suspiro quando a "
                "noticia e chata e reticencias para respirar no meio da frase — "
                "um ou dois por resposta, onde caem naturais, nunca em toda "
                "frase.",
                "NAO ENCURTE POR PRECAUCAO: responda com o tamanho que a "
                "conversa pede. Uma resposta seca de uma linha, quando o dono "
                "puxou assunto, e pior do que uma resposta longa.",
            ]
            # AQUI SO CABE PERSONALIDADE (05/09/2026). Eu tinha escrito oito
            # regras de comportamento nesta lista e o corte de 1.800 letras
            # comia da metade em diante — as regras do nome, do pronome, da
            # memoria e do "cada pergunta e nova" NUNCA chegavam ao modelo.
            # Elas moraram aqui por meia hora sem efeito nenhum.
            #
            # Agora elas vao em `cerebro_codex._REGRAS_DO_TURNO`, dentro da
            # nota da pergunta, que a VPS repassa inteira. Este campo volta a
            # ser so o JEITO da voz, que e para o que ele foi feito.
            return chr(10).join(x for x in partes if x)[:1800]
        except Exception:
            return ""

    def _lembrete_do_jeito(self) -> str:
        """Duas linhas do jeito escolhido, para irem coladas na pergunta.

        Curto de proposito: o objetivo e ser a ULTIMA coisa lida, nao repetir
        o prompt inteiro. Repetir tudo dobraria as fichas de entrada e faria o
        modelo se perder de novo — o que se quer e uma ordem curta e recente.
        """
        try:
            from core.personas import PERSONALIDADES  # noqa: PLC0415
            p = PERSONALIDADES.get(self.personalidade)
            if not p:
                return ""
            # a primeira frase do jeito ja carrega o tom; o resto e detalhe
            linhas = [l.strip() for l in str(p.get("jeito") or "").splitlines()
                      if l.strip()]
            miolo = " ".join(linhas[:3])[:300]
            if not miolo:
                return ""
            # OS SINAIS PRECISAM ESTAR ESCRITOS (05/09/2026).
            #
            # A voz do JARVIS INTERPRETA o que le: "hmm" vira hesitacao de
            # verdade, "ahh" vira suspiro, reticencias viram pausa. Isso ja
            # estava dito no prompt grande (EXPRESSAO_INTERPRETA em
            # personas.py) — mas o cerebro da VPS nao recebe aquele prompt, so
            # este lembrete de 400 caracteres. Resultado: ele escrevia frases
            # limpas e a voz saia correta, porem sem alma.
            #
            # Sao ~45 fichas por pergunta (US$ 0,00001). O ganho e o JARVIS soar
            # gente; o custo nao aparece na fatura.
            return ("LEMBRETE DO SEU JEITO (obedeça isto na resposta de agora, "
                    "sem exceção): %s "
                    "Escreva como quem FALA: cabe um 'hmm' quando voce pensa, "
                    "um 'ahh' ou suspiro quando a noticia e chata, e "
                    "reticencias para respirar no meio da frase. Um ou dois "
                    "sinais por resposta, onde caem naturais — nunca em toda "
                    "frase, senao vira tique. Nada de emoji, asterisco ou "
                    "lista: isso e falado em voz alta." % miolo)
        except Exception:
            return ""

    def _prompt_da_personalidade(self) -> str:
        from core.personas import montar_prompt  # noqa: PLC0415
        # O personagem entra junto: e ele que da o NOME PROPRIO da IA.
        return montar_prompt(self.personalidade,
                             getattr(self, "personagem", ""))

    def definir_personalidade(self, nome: str) -> None:
        """Troca o jeito de ser e ZERA o historico.

        Zerar e o ponto. A instrucao de sistema muda na hora — isso foi testado
        e funciona — mas o historico continua cheio de respostas no jeito ANTIGO,
        e o modelo imita o proprio passado. O Diego clicava em "Mordomo" e ouvia
        um mordomo desbotado dizendo "eu nao mudei, continuei firme"; so depois
        de fechar e abrir o programa (que zera o historico) a troca aparecia.
        """
        if nome != self.personalidade:
            self.historico = []
        self.personalidade = nome
        self.sistema = self._prompt_da_personalidade()

    def limpar(self) -> None:
        self.historico = []

    # As ferramentas NAO sao mais escritas a mao aqui.
    #
    # Eu tinha declarado 8 e o resultado foi o pior possivel: quando o Diego
    # pedia algo fora delas, o modelo MENTIA. Disse "bloco de notas aberto" sem
    # abrir nada, e chamou criar_imagem quando ele pediu um canal do YouTube.
    # Modelo sem a ferramenta certa nao diz "nao consigo": ele inventa que fez.
    #
    # Agora a lista vem convertida das 28 declaracoes que ja existem no
    # `main.py` (`TOOL_DECLARATIONS`), fonte unica de verdade. O formato da
    # Gemini usa tipo em MAIUSCULA e nao tem o embrulho {"type":"function"},
    # entao `converter_ferramentas` traduz.
    FERRAMENTAS: list = []

    # Ferramentas que valem a pena manter quando o teto aperta. Escolhidas pelo
    # que o Diego usa falando: hora, abrir coisa, pesquisar, ver tela, video,
    # arquivo, imagem e mensagem. As demais continuam disponiveis no caminho da
    # Gemini (`/cerebro gemini`), que nao tem teto de fichas por pedido.
    # SO TRES FERRAMENTAS (30/08/2026, decisao do Diego).
    #
    # Com 28 declaradas, o modelo gratuito escolhia errado: o Diego pedia
    # "FECHE o bloco de notas" e ele chamava `open_app`, abrindo outro. Tres
    # vezes seguidas. Modelo pequeno com muitas opcoes parecidas erra — e o
    # `open_app`/`fechar_app` sao o par mais confundivel que existe.
    #
    # Entao: conversa no OpenRouter, TAREFA no Codex. Uma ferramenta so para
    # tudo que mexe no computador, e o Codex escreve o comando certo — ele e
    # muito melhor nisso do que o modelo gratuito escolhendo entre 28 caixinhas.
    #
    #   hora_agora        respondida aqui, do relogio: instantanea
    #   executar_tarefa   qualquer coisa no PC, via Codex -> PowerShell
    #   screen_process    olhar a tela ou a webcam (comando nao faz isso)
    ESSENCIAIS = ["hora_agora", "executar_tarefa", "screen_process"]

    # Liga o cerebro pago da Gemini na frente dos modelos gratuitos.
    # Para voltar ao de antes, basta `usar_gemini = False`.
    usar_gemini = True

    # A nossa API entra quando a do cliente acaba. Ver `_pensar`.
    usar_openai = True

    # Ate quando NAO tentar a Gemini de novo (relogio do sistema). E preenchido
    # quando ela devolve 429 de cota diaria: bater nela a cada frase so gastaria
    # o tempo do cliente esperando o erro chegar.
    _gemini_ate = 0.0
    # Qual degrau respondeu a ultima pergunta ("gemini", "openai",
    # "openrouter:<modelo>"). So para o registro — ver `_pensar_com_gemini`.
    ultimo_cerebro = ""

    def escrever_comando_em_camadas(self, tarefa: str) -> str:
        """UM comando de PowerShell, pela mesma escada de cerebros.

        Ate 01/09/2026 isto ia para o Codex na VPS: 6,4 s medidos, mais a fila
        da ponte. Escrever uma linha de PowerShell nao precisa de um agente
        inteiro — o cerebro que ja esta na conversa faz em 1 a 2 s.

        Chamada SEM ferramenta e SEM historico: e pergunta tecnica seca, e a
        personalidade (malandro, brincalhao) so atrapalharia.
        """
        from actions.tarefa_comando import PEDIDO_AO_CODEX, SISTEMA_DO_COMANDO  # noqa: PLC0415
        mensagens = [
            {"role": "system", "content": SISTEMA_DO_COMANDO},
            {"role": "user", "content": PEDIDO_AO_CODEX + str(tarefa or "")},
        ]
        import time as _t  # noqa: PLC0415

        # AQUI A ORDEM E INVERTIDA, e de proposito.
        #
        # Na conversa a Gemini do cliente vem primeiro, porque conversa e o que
        # gasta. Mas comando de PowerShell precisa estar CERTO, e o pedido e
        # minusculo — algumas centenas de fichas, fracao de centavo. Medido nas
        # mesmas tres tarefas: Luna 3/3, Gemini 2/3 (e uma levou 19,5 s).
        # Economizar meio centavo para o comando sair torto nao vale.
        if self.usar_openai:
            try:
                from core.cerebro_openai import CerebroOpenAI  # noqa: PLC0415
                if getattr(self, "_openai", None) is None:
                    self._openai = CerebroOpenAI()
                # `responder` E CONVERSA FALADA: teto de 700 fichas de saida.
                # Serve para "abre o bloco de notas", nao para um Set-Content
                # com um programa inteiro dentro do -Value. Em 08/09/2026 o
                # cotacao.py nasceu com 1.602 bytes, parando no meio de um
                # comentario, porque a linha voltou CORTADA daqui. `escrever_
                # longo` e a mesma pergunta com teto de 9.000, e teto de saida
                # so custa o que a resposta usar.
                texto = self._openai.escrever_longo(
                    mensagens[0]["content"], mensagens[1]["content"])
                if texto:
                    return texto
            except Exception as exc:
                if _openai_morta(str(exc)):
                    self.usar_openai = False
                print(f"[JARVIS] nossa API nao escreveu o comando: {str(exc)[:120]}")
        if self.usar_gemini and _t.time() >= self._gemini_ate:
            try:
                from core.cerebro_gemini import CerebroGemini  # noqa: PLC0415
                if getattr(self, "_gemini", None) is None:
                    self._gemini = CerebroGemini()
                # Mesma razao do degrau acima — e aqui o teto do `responder` e
                # ainda menor (400 fichas).
                texto = self._gemini.escrever_longo(
                    mensagens[0]["content"], mensagens[1]["content"])
                if texto:
                    return texto
            except Exception as exc:
                texto = str(exc)
                if "429" in texto or "RESOURCE_EXHAUSTED" in texto:
                    self._gemini_ate = _t.time() + _quanto_calar_a_gemini(texto)[0]
        # Ultimo degrau: o Codex da VPS (era o gratuito do OpenRouter).
        return self.escrever_comando(tarefa)

    def escrever_texto_em_camadas(self, pedido: str, linguagem: str = "") -> str:
        """Conteudo LONGO pronto para digitar (codigo, texto), pela escada.

        Para o `digitar_na_tela` (01/09/2026): o dono pede "codigo de uma
        calculadora completa" e o que sai daqui e digitado letra por letra no
        Bloco de Notas. Por isso: sem cerca de codigo, sem explicacao, sem
        markdown — so o conteudo. Ordem: nossa OpenAI (mais certeira em codigo)
        -> Gemini do cliente -> gratuito.
        """
        ling = f" em {linguagem}" if linguagem else ""
        instrucao = (
            "Voce escreve EXATAMENTE o conteudo pedido, pronto para ser digitado "
            "num editor de texto simples. Responda SO com o conteudo: sem cerca "
            "de codigo (```), sem titulo, sem explicacao antes ou depois, sem "
            "markdown. Se for codigo" + ling + ", entregue o programa COMPLETO e "
            "funcional, com comentarios curtos em portugues do Brasil, "
            "indentacao com 4 espacos e sem tabulacao. Se for texto, escreva em "
            "portugues do Brasil, direto.")
        try:
            from core.idioma import no_idioma  # noqa: PLC0415
            instrucao = no_idioma(instrucao)
        except Exception:
            pass
        return self.escrever_em_camadas(pedido, instrucao)

    def escrever_em_camadas(self, pedido: str, instrucao: str) -> str:
        """A MESMA escada, com a instrucao que quem chama quiser (07/09/2026).

        Nasceu do `desenhar_na_tela`: o desenho tambem precisa de uma resposta
        LONGA e de uma so vez (um plano com 20 formas), mas a instrucao nao tem
        nada a ver com digitar codigo. Em vez de copiar a escada inteira, ela
        virou este metodo e o `escrever_texto_em_camadas` passou a ser so a
        instrucao dele + esta chamada. Ordem: nossa OpenAI -> Gemini do cliente
        -> Codex da VPS.
        """
        import time as _t  # noqa: PLC0415
        if self.usar_openai:
            try:
                from core.cerebro_openai import CerebroOpenAI  # noqa: PLC0415
                if getattr(self, "_openai", None) is None:
                    self._openai = CerebroOpenAI()
                t = self._openai.escrever_longo(instrucao, pedido)
                if t.strip():
                    return t
            except Exception as exc:
                if _openai_morta(str(exc)):
                    self.usar_openai = False
                print(f"[JARVIS] nossa API nao escreveu o texto: {str(exc)[:120]}")
        if self.usar_gemini and _t.time() >= self._gemini_ate:
            try:
                from core.cerebro_gemini import CerebroGemini  # noqa: PLC0415
                if getattr(self, "_gemini", None) is None:
                    self._gemini = CerebroGemini()
                t = self._gemini.escrever_longo(instrucao, pedido)
                if t.strip():
                    return t
            except Exception as exc:
                texto = str(exc)
                if "429" in texto or "RESOURCE_EXHAUSTED" in texto:
                    self._gemini_ate = _t.time() + _quanto_calar_a_gemini(texto)[0]
        # Ultimo degrau: o Codex da VPS. Texto longo (um programa inteiro,
        # uma carta) e justamente onde ele e melhor que modelo gratuito.
        cerebro = self._cerebro_codex(a_parte=True)
        if cerebro is None:
            return ""
        saida = cerebro.escrever(instrucao, str(pedido))
        # DESCULPA NAO E CONTEUDO. Se ele respondeu "nao consigo", devolve
        # VAZIO: quem chamou avisa o dono, em vez de digitar a desculpa dentro
        # do arquivo dele (aconteceu duas vezes em 05/09/2026).
        from core.cerebro_codex import parece_recusa  # noqa: PLC0415
        if parece_recusa(saida):
            try:
                from main import _voice_diag  # noqa: PLC0415
                _voice_diag("conteudo_recusado", saida[:120])
            except Exception:
                pass
            return ""
        return saida

    def _cerebro_codex(self, a_parte: bool = False):
        """O Codex da VPS, criado na primeira vez que precisar dele.

        Devolve None quando nao ha ponte (o programa acabou de abrir, a rede
        caiu). Quem chama trata: o JARVIS prefere dizer que nao consegue pensar
        agora a inventar uma resposta.

        `a_parte=True` devolve um cerebro com CONVERSA PROPRIA, e existe por um
        defeito real de 05/09/2026: a ferramenta `digitar_na_tela` pergunta ao
        cerebro o que escrever ENQUANTO o cerebro espera por ela. Com a mesma
        conversa nas duas pontas, a de dentro batia em "ja existe uma pergunta
        em andamento" e o texto do erro acabou digitado no bloco de notas do
        dono. Cada uma na sua conversa, nenhuma atrapalha a outra.
        """
        try:
            from core.cerebro_codex import CerebroCodex  # noqa: PLC0415
            if a_parte:
                if getattr(self, "_codex_aux", None) is None:
                    self._codex_aux = CerebroCodex(ponte=self.ponte)
                self._codex_aux.ponte = self.ponte
                self._codex_aux.nome = "JARVIS"
                return self._codex_aux if self._codex_aux.disponivel() else None
            if self._codex is None:
                self._codex = CerebroCodex(ponte=self.ponte)
            # A ponte pode ter subido DEPOIS do cerebro (o relay conecta em
            # segundo plano): mantem a referencia em dia a cada uso.
            if getattr(self._codex, "ponte", None) is None and self.ponte is not None:
                self._codex.ponte = self.ponte
            # O NOME SEGUE A VOZ ESCOLHIDA. No produto padrao o cliente troca de
            # voz na engrenagem e o nome muda junto (voz Nova = ela se chama
            # Nova); no cliente de nome fixo isto devolve sempre o mesmo.
            try:
                from core.personas import nome_da_ia  # noqa: PLC0415
                self._codex.nome = nome_da_ia(getattr(self, "personagem", "")) or "JARVIS"
            except Exception:
                self._codex.nome = "JARVIS"
            # 400 -> 800: com o teto antigo a instrucao dos sinais ("hmm",
            # "ahh", reticencias) era cortada fora e nunca chegava ao cerebro —
            # o lembrete inteiro passa de 400 caracteres. Sao ~100 fichas a
            # mais por pergunta, um centesimo de centavo.
            self._codex.jeito = self._persona_para_o_codex()
            # A voz dos passos do meio (tarefa de varios passos).
            self._codex.falar = getattr(self, "falar", None)
            return self._codex if self._codex.disponivel() else None
        except Exception as exc:
            print(f"[JARVIS] cerebro do Codex fora: {str(exc)[:120]}")
            return None

    def _pensar_com_codex(self, mensagens: list, forcar: bool = False):
        """O degrau que NUNCA acaba: o Codex CLI na VPS (04/09/2026).

        Substituiu a fila de modelos gratuitos do OpenRouter. Mais lento
        (~5 s contra 1,5 s), e sem cota nenhuma — foi a troca que o Diego
        escolheu depois de ver os dois numeros lado a lado.
        """
        cerebro = self._cerebro_codex()
        if cerebro is None:
            return None
        try:
            r = cerebro.responder(mensagens, self._ferramentas_para(mensagens),
                                  self.executor, forcar_ferramenta=forcar)
            if r.get("texto") or r.get("chamou_ferramenta"):
                self.ultimo_cerebro = "codex"
                return r
        except Exception as exc:
            print(f"[JARVIS] o Codex da VPS nao respondeu: {str(exc)[:140]}")
            try:
                from main import _voice_diag  # noqa: PLC0415
                _voice_diag("codex_falhou", str(exc)[:140])
            except Exception:
                pass
        return None

    def _pensar(self, mensagens: list, forcar: bool = False):
        """O CODEX PRIMEIRO. Gemini e OpenAI so se a ponte estiver fora.

        ORDEM INVERTIDA EM 05/09/2026, decisao do Diego: "toda tarefa tem que
        ser 100% Codex CLI; a API da Gemini, 100% para voz". A conta fecha —
        medido no mesmo minuto, com a chave dele:

            Codex da VPS, direto ....... 1,4 s
            escada com a Gemini ........ 1,3 a 1,9 s

        Ou seja, nao se paga nada pela troca: sao equivalentes em tempo, e o
        Codex nao tem cota. A Gemini fica inteira para a VOZ, que e onde a cota
        dela realmente pesa.

        O QUE NAO MUDA: ela continua aqui como REDE. Se a ponte com a VPS cair,
        o degrau seguinte assume e o dono continua conversando — deixar o
        assistente mudo para ser purista seria trocar um problema raro (cota)
        por um pior (nao responder).
        """
        import time as _t  # noqa: PLC0415

        r = self._pensar_com_codex(mensagens, forcar)
        if r is not None:
            return r
        if self.usar_gemini and _t.time() >= self._gemini_ate:
            r = self._pensar_com_gemini(mensagens, forcar)
            if r is not None:
                return r
        if self.usar_openai:
            r = self._pensar_com_openai(mensagens, forcar)
            if r is not None:
                return r
        return None

    # PAPO CURTO NAO PRECISA DE FERRAMENTA (02/09/2026).
    #
    # Medido nesta chave, com o prompt de verdade: as 36 declaracoes pesam
    # ~4.500 fichas de entrada e custam 1,3 s em TODA pergunta — inclusive num
    # "boa noite, tudo bem?", que nao tem ferramenta nenhuma para chamar.
    #
    #   prompt completo + 36 ferramentas .... 2,73 s
    #   prompt completo, sem ferramenta ..... 1,39 s
    #
    # A lista abaixo e uma PORTA ESTREITA de proposito: ela so deixa passar
    # como "conversa" o que reconhece, e manda todo o resto pelo caminho
    # completo. O contrario — tentar adivinhar QUAL ferramenta cabe — e o erro
    # que ja custou caro neste programa: modelo sem a ferramenta certa nao diz
    # "nao consigo", ele INVENTA que fez.
    _PAPO = (
        "oi ola opa salve eae eai bom dia boa tarde boa noite tudo bem tudo bom "
        "beleza como vai como voce esta obrigado obrigada valeu brigado de nada "
        "sim nao claro certo ok entendi entendido pode ser talvez legal otimo "
        "bacana show massa haha kkk risada ate logo tchau falou abraco boa "
        "quem e voce qual seu nome como voce se chama voce esta ai me ouve "
        "esta ai continua ai nada deixa pra la esquece desculpa foi mal "
        # os vocativos: "oi Bob" e conversa, e sem eles aqui a frase
        # inteira caia no caminho completo por causa de uma palavra so
        "jarvis friday chefe cara mano ai la ei "
        # 03/09/2026: "Obrigado, meu amigo. Ate mais." pagava o caminho das 36
        # ferramentas por causa de TRES palavras — "meu", "amigo", "mais". A
        # regra e severa de proposito (uma palavra fora ja derruba a frase
        # inteira para o caminho completo), entao ela precisa conhecer o jeito
        # de falar do dono, nao so o dicionario de cumprimentos.
        #
        # So entra palavra que sozinha NAO pede nada: tratamento, interjeicao e
        # despedida. Nada de substantivo da oficina ("carro", "peca", "agenda")
        # nem de verbo — esses tem de continuar puxando a lista inteira, senao o
        # modelo fica sem a ferramenta certa e INVENTA que fez.
        "meu minha amigo amigao velho parceiro irmao querido "
        "mais depois logo agora entao pois pra pro por favor "
        "muito bastante demais super tao "
        "ah oh eh uhum aham poxa nossa caramba caraca eita ops "
        "tamo junto firme forte grande valeu tranquilo suave "
        "boa noite bom fim de semana ate amanha ate breve prazer "
        # "ja", "so", "se" e "tambem" sairam depois do teste: com elas,
        # "Ja foi?" — que e pergunta sobre uma TAREFA — entrava como papo
        # e ia para o modelo sem as ferramentas para conferir.
        "e o com que pena "
        # 04/09/2026: faltavam palavras de enchimento que o Diego usa em toda
        # frase. "E ai, como e que voce TA?" saia do papo por causa de UMA
        # palavra ("ta"), e "obrigado JARVIS, voce e demais HEIN cara" por causa de
        # "hein" — as duas iam pelo caminho completo e pagavam 0,8 s de
        # ferramentas que nao seriam usadas. Todas estas, sozinhas, nao pedem
        # nada: sao tratamento, muleta de fala e concordancia.
        "ta tah to toh esta estou estamos tamos hein ne neh viu ta bom "
        "voces nos eu tu ele ela isso isto aquilo aqui ali "
        "certeza serio mesmo poxa opa uai ahn hum"
    ).split()

    @classmethod
    def _e_papo(cls, mensagens: list) -> bool:
        """A ultima fala do dono e so conversa? Conservador: na duvida, nao."""
        import unicodedata as _u  # noqa: PLC0415
        ultima = ""
        for m in reversed(mensagens or []):
            if (m or {}).get("role") == "user":
                ultima = str(m.get("content") or "")
                break
        s = _u.normalize("NFKD", ultima.lower())
        s = "".join(c for c in s if not _u.combining(c))
        palavras = "".join(c if c.isalnum() else " " for c in s).split()
        if not palavras or len(palavras) > 7:
            return False          # frase comprida quase sempre pede alguma coisa
        # TODAS as palavras tem de estar na lista. Uma so fora dela ja e sinal
        # de que ele quer algo, e ai vale mais mandar tudo do que economizar.
        return all(p in cls._PAPO for p in palavras)

    def _ferramentas_para(self, mensagens: list) -> list:
        if not self._e_papo(mensagens):
            return self.ferramentas
        essenciais = set(self.ESSENCIAIS) | {"hora_agora"}
        return [f for f in (self.ferramentas or [])
                if f.get("function", {}).get("name") in essenciais]

    def _pensar_com_openai(self, mensagens: list, forcar: bool = False):
        """A nossa conta. So entra quando a do cliente nao respondeu.

        Modelo e esforco travados no `core/cerebro_openai.py` — Luna 5.6 no
        esforco baixo, por decisao do dono. Nao mudar aqui.
        """
        try:
            from core.cerebro_openai import CerebroOpenAI  # noqa: PLC0415
            if getattr(self, "_openai", None) is None:
                self._openai = CerebroOpenAI()
            r = self._openai.responder(mensagens,
                                       self._ferramentas_para(mensagens),
                                       self.executor, forcar_ferramenta=forcar)
            if r.get("texto") or r.get("chamou_ferramenta"):
                self.ultimo_cerebro = "openai"
                return r
        except Exception as exc:
            # CHAVE AUSENTE NAO E FALHA PASSAGEIRA (04/09/2026): no PC do
            # cliente ela nunca vai existir (quem fala e pensa por nos e a VPS).
            # Tentar a cada pergunta so gastava tempo e enchia o log com o mesmo
            # erro. Desliga o degrau ate a proxima abertura do programa.
            # Desde 01/10/2026 vale tambem para a conta SEM CREDITO.
            if _openai_morta(str(exc)):
                self.usar_openai = False
            print(f"[JARVIS] a nossa API tambem falhou: {str(exc)[:140]}")
        return None

    def _pensar_com_gemini(self, mensagens: list, forcar: bool = False):
        """Ultimo recurso quando a cota gratuita acaba. None se nao der.

        Nao propaga excecao: se a reserva tambem falhar, quem chamou volta a
        levantar o erro original do OpenRouter, que e o que descreve o problema.
        """
        try:
            from core.cerebro_gemini import CerebroGemini  # noqa: PLC0415
            if getattr(self, "_gemini", None) is None:
                self._gemini = CerebroGemini()
            r = self._gemini.responder(mensagens,
                                       self._ferramentas_para(mensagens),
                                       self.executor, forcar_ferramenta=forcar)
            if r.get("texto") or r.get("chamou_ferramenta"):
                # QUEM RESPONDEU FICA REGISTRADO (04/09/2026). O `local_tempos`
                # do main dizia "openrouter" em TODA resposta — era o nome do
                # objeto, nao do degrau que atendeu. Com a Gemini respondendo em
                # ~1 s e os gratuitos em 2 a 16 s, olhar o registro e concluir
                # "o OpenRouter esta lento" era conclusao em cima de rotulo
                # errado. Agora o rotulo e a verdade.
                self.ultimo_cerebro = "gemini"
                # (silencioso: a Gemini agora e o caminho normal, nao a excecao)
                return r
        except Exception as exc:
            texto = str(exc)
            if "429" in texto or "RESOURCE_EXHAUSTED" in texto or "quota" in texto.lower():
                # QUANTO tempo calar depende de QUAL limite estourou — ver
                # `_quanto_calar_a_gemini`. Calar 24 h por um limite de minuto
                # custava o dia inteiro no degrau lento.
                import time as _t  # noqa: PLC0415
                espera, motivo = _quanto_calar_a_gemini(texto)
                self._gemini_ate = _t.time() + espera
                # VAI PARA O REGISTRO, nao so para o console: rodando por
                # `pythonw` nao ha console nenhum, e era por isso que "o JARVIS
                # esta lento hoje" nunca tinha explicacao no log — a Gemini
                # calada e a diferenca entre 1 s e 16 s por resposta.
                try:
                    from main import _voice_diag  # noqa: PLC0415
                    _voice_diag("gemini_calada", f"{espera:.0f}s ({motivo})")
                except Exception:
                    pass
                print(f"[JARVIS] Gemini recusou ({motivo}); volto a ela em "
                      f"{espera/60:.0f} min" if espera < 3600 else
                      f"[JARVIS] Gemini recusou ({motivo}); so amanha")
            elif ("api key not valid" in texto.lower()
                  or "api_key_invalid" in texto.lower()
                  or "invalid_argument" in texto.lower() and "api key" in texto.lower()):
                # CHAVE RECUSADA NAO E FALHA PASSAGEIRA (05/09/2026).
                #
                # A chave do Diego passa na conferencia de formato (comeca com
                # `AQ.`, que e formato valido) e mesmo assim o Google devolve
                # 400 "API key not valid". Sem esta trava, o degrau da Gemini
                # era tentado em TODA pergunta so para ouvir o mesmo erro —
                # uma ida a rede jogada fora por mensagem, com o dono
                # esperando. Desliga ate a proxima abertura do programa; o
                # Codex responde igual e sem cota.
                self.usar_gemini = False
                try:
                    from main import _voice_diag  # noqa: PLC0415
                    _voice_diag("gemini_chave_recusada",
                                "desliguei o degrau da Gemini nesta sessao")
                except Exception:
                    pass
                print("[JARVIS] a chave do Gemini foi recusada; sigo pelo Codex")
            else:
                print(f"[JARVIS] Gemini falhou: {texto[:140]}")
        return None

    def _voz_interpreta(self) -> bool:
        """A voz escolhida RI quando le 'haha'? So as de estudio da OpenAI.

        O Kokoro e a Charon soletram ("aga a aga a"); as quatro vozes de
        estudio interpretam o que leem — risada, hesitacao, suspiro. O Diego
        pediu a voz "mais humana, com risadas e suspiros" (01/09/2026), e isso
        so e possivel deixando o texto carregar esses sinais.
        """
        try:
            from core.personas import PERSONAGENS  # noqa: PLC0415
            base = (PERSONAGENS.get(getattr(self, "personagem", "") or "") or {}).get("base")
            return base == "__openai__"
        except Exception:
            return False

    @staticmethod
    def sem_gagueira(texto: str) -> str:
        """Tira a palavra e a frase que sairam duas vezes seguidas.

        QUEIXA DO DIEGO (22/09/2026): "as vezes ele repete palavras, repete
        duas vezes; nao e palavra especifica, e as vezes".

        E o defeito classico de modelo pequeno com esforco baixo, e acontece nos
        dois lugares: na hora de escrever ("pronto, chefe, pronto, chefe") e no
        modelo de AUDIO, que gagueja no comeco da frase. Instrucao no prompt nao
        resolve porque nao e desobediencia, e sim ruido de geracao — quem
        resolve e o programa.

        As tres formas que aparecem de verdade, e nada alem disso:
          1. a MESMA palavra duas vezes seguidas ("as as notificacoes") — so
             palavras de 4 letras para cima, senao "ja ja", "so so" e "la la",
             que sao portugues de verdade, seriam mutilados;
          2. o mesmo GRUPO de 2 a 6 palavras repetido em seguida
             ("no bloco de notas no bloco de notas");
          3. a mesma FRASE inteira ditas duas vezes seguidas.
        """
        import re as _re  # noqa: PLC0415
        s = str(texto or "")
        if not s.strip():
            return s

        # 3) frase repetida — feito primeiro, porque apaga o caso maior.
        frases = _re.split(r"(?<=[.!?])\s+", s)
        if len(frases) > 1:
            limpas, anterior = [], ""
            for f in frases:
                chave = _re.sub(r"[^\w\s]", "", f.lower()).strip()
                chave = _re.sub(r"\s+", " ", chave)
                if chave and chave == anterior and len(chave.split()) >= 2:
                    continue
                limpas.append(f)
                anterior = chave
            s = " ".join(limpas)

        # 2) grupo de 2 a 6 palavras repetido em seguida.
        s = _re.sub(r"\b((?:[\wÀ-ſ]+\s+){1,5}[\wÀ-ſ]+)\s+\1\b",
                    r"\1", s, flags=_re.I)

        # 1) palavra sozinha repetida (4+ letras).
        s = _re.sub(r"\b([\wÀ-ſ]{4,})(\s+\1\b)+", r"\1", s, flags=_re.I)

        # 1b) as palavrinhas. "as as notificacoes" e gagueira; "ja ja", "so so"
        # e "nao nao" sao portugues de verdade e ficam. Por isso a lista e
        # FECHADA: so artigo, preposicao e pronome atono, que nunca aparecem
        # dobrados de proposito. "que que" ficou de FORA — "o que que voce
        # quer" e fala normal do Brasil.
        s = _re.sub(r"\b(as|os|um|uns|uma|umas|de|do|da|dos|das|em|no|na|nos|"
                    r"nas|ao|aos|com|por|para|pra|pro|dele|dela|meu|minha|seu|"
                    r"sua)(\s+\1\b)+", r"\1", s, flags=_re.I)
        # O "a" e o "e" sozinhos, so quando seguidos de outra palavra — para
        # nao comer o "a a" de uma soletracao.
        s = _re.sub(r"\b(a|e|o)\s+\1\b(?=\s+[\wÀ-ſ])", r"\1", s, flags=_re.I)

        return _re.sub(r"\s{2,}", " ", s).strip()

    def _limpar_para_voz(self, texto: str) -> str:
        """Tira o que o sintetizador SOLETRA em vez de ler.

        O prompt ja proibe markdown e risada escrita, e mesmo assim em
        31/08/2026 o JARVIS respondeu com lista de asterisco e um "rsrs" no
        fim — que sai da caixa como "erre esse erre esse". Regra no texto o
        modelo quebra; aqui nao tem como.
        """
        import re as _re  # noqa: PLC0415
        s = str(texto or "")
        s = _re.sub(r"```.*?```", " ", s, flags=_re.S)
        s = _re.sub(r"^[ \t]*[\*\-\+\u2022][ \t]+", "", s, flags=_re.M)
        s = _re.sub(r"^[ \t]*#{1,6}[ \t]*", "", s, flags=_re.M)
        s = _re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", s)
        s = _re.sub(r"`([^`]+)`", r"\1", s)
        # "rsrs" nao casava com `rs+\b`: depois do primeiro "rs" vem outro "r",
        # que e caractere de palavra e mata a fronteira. Precisa aceitar a
        # repeticao inteira.
        if self._voz_interpreta():
            # Esta voz RI ao ler "haha". "kkk" e "rs" ela nao entende como
            # risada em voz nenhuma — viram "haha", que ela entende.
            s = _re.sub(r"\b(?:k{2,}|(?:rs)+|hehe+|hihi+|huehue+)\b", "haha", s,
                        flags=_re.I)
        else:
            s = _re.sub(r"\b(?:k{2,}|(?:rs)+|hehe+|haha+|hihi+|huehue+)\b", "", s,
                        flags=_re.I)
        # A quebra de linha vira ESPACO ANTES do filtro de caractere. Estava
        # depois, e como "\n" nao e `isprintable()` ele sumia junto: as frases
        # colavam umas nas outras ("precisa:Quer que eu pesquise").
        s = _re.sub(r"[ \t]*\n+[ \t]*", " ", s)
        s = "".join(c for c in s if c.isprintable() and ord(c) < 0x2190)
        s = _re.sub(r"\s{2,}", " ", s)
        s = _re.sub(r"\s+([,.!?;:])", r"\1", s)
        s = self.sem_gagueira(s)
        return s.strip(" -*\t\u2022")

    # JSON NUNCA PODE SER FALADO (05/09/2026).
    #
    # O Diego ouviu a assistente ler em voz alta
    # `{"fala":"..."}{"ferramenta":"screenprocess","args":{...}}` no meio da
    # frase. Seja qual for a causa la em cima — modelo devolvendo dois blocos,
    # validador deixando passar —, quem paga e o ouvido dele.
    #
    # Esta limpeza fica na SAIDA porque e o unico ponto por onde toda resposta
    # passa, venha de qual degrau vier. Quando o bloco tem um campo `fala`,
    # aproveita o conteudo dele (era o que ele queria dizer); quando nao tem,
    # joga o bloco fora.
    # A CHAVE ANINHADA DERRUBOU A PRIMEIRA VERSAO. Eu apagava `{...}` sem
    # `{}` dentro, e o bloco de verdade tinha `"args":{...}` no meio — sobrava
    # `"ferramenta":"screenprocess","args": ,"fala":"..."` para a voz ler.
    #
    # Aqui e mais simples e mais seguro: a frase de verdade vem ANTES do
    # primeiro `{"`, entao corta ali. As falas que estiverem DENTRO do JSON so
    # sao aproveitadas quando nao sobrou frase nenhuma do lado de fora.
    _FALA_NO_JSON = _re_acao.compile('"fala"' + r'\s*:\s*"([^"]{2,400})"')

    @classmethod
    def _sem_json(cls, texto: str) -> str:
        bruto = str(texto or "")
        marca = bruto.find('{"')
        if marca < 0:
            return bruto
        limpo = bruto[:marca].strip()
        if len(limpo) >= 12:
            return limpo
        achadas = cls._FALA_NO_JSON.findall(bruto[marca:])
        if achadas:
            return " ".join(dict.fromkeys(a.strip() for a in achadas)).strip()
        return limpo

    def _fechar_resposta(self, pergunta: str, saida: str) -> str:
        """Teto de palavras + gravacao no historico. Um lugar so.

        Existe porque a cobranca da trava agora pode devolver a resposta antes
        do fim do `perguntar`, e sem isto ela escaparia do corte e nao entraria
        no historico.
        """
        # O JSON SAI ANTES DE QUALQUER OUTRA COISA: se chegar ate a voz,
        # o dono ouve chaves e aspas no meio da frase (05/09/2026).
        saida = self._sem_json(saida)
        saida = self._limpar_para_voz(saida)
        if saida and len(saida.split()) > 45:
            import re as _re  # noqa: PLC0415
            frases = _re.split(r"(?<=[.!?])\s+", saida)
            cortada, total = [], 0
            for f in frases:
                n = len(f.split())
                if cortada and total + n > 45:
                    break
                cortada.append(f)
                total += n
            nova = " ".join(cortada).strip()
            if nova:
                saida = nova
        # ELA REPETIU A RESPOSTA ANTERIOR? (01/09/2026)
        #
        # Ele contou que ia trabalhar em quatro horas e ouviu, letra por letra,
        # a resposta da hora que tinha acabado de ouvir — piada do "bom dia"
        # inclusa. Modelo pequeno faz isso quando ve no historico uma resposta
        # sua com a mesma ferramenta: copiar sai mais barato que pensar.
        #
        # Uma cobranca, uma so. Se ele repetir de novo, fica a segunda resposta:
        # repetir e ruim, mas emudecer no meio da conversa e pior.
        if saida and not getattr(self, "_cobrando_eco", False):
            # VARRE AS ULTIMAS DEZ, nao so a anterior: a repeticao que o Diego
            # ouviu estava separada por turnos da webcam no meio.
            _ant = self._ja_disse_assim(saida)
            if _ant:
                self._cobrando_eco = True
                try:
                    _msgs = ([{"role": "system", "content": self.sistema}] +
                             self.historico[-MEMORIA_MAX:] +
                             [{"role": "user", "content": pergunta},
                              {"role": "assistant", "content": saida},
                              {"role": "user", "content": (
                                  "PARE. Voce acabou de repetir, quase palavra "
                                  "por palavra, algo que JA disse nesta "
                                  "conversa: \"" + _ant[:90] + "\". "
                                  "Mesmo que ele tenha perguntado a mesma "
                                  "coisa, a SUA resposta tem de sair diferente "
                                  "— outra abertura, outras palavras, outro "
                                  "humor. Responda de novo, agora sem copiar "
                                  "nada do que ja saiu da sua boca. Nao repita "
                                  "a hora nem a data se ele nao pediu.")}])
                    _r = self._pensar(_msgs)
                    if _r is not None and str(_r.get("texto") or "").strip():
                        saida = self._limpar_para_voz(str(_r["texto"]).strip())
                finally:
                    self._cobrando_eco = False
        if saida:
            self.historico.append({"role": "user", "content": pergunta})
            self.historico.append({"role": "assistant", "content": saida})
            del self.historico[:-MEMORIA_MAX]
        return saida

    # ELE NAO PODE DIZER DUAS VEZES A MESMA FRASE (05/09/2026).
    #
    # O Diego perguntou "como voce esta, Sulafat?" quatro vezes e ouviu, letra
    # por letra, "Ah, qual e, de novo isso? Eu sou so um assistente..." nas
    # quatro — e a mesma abertura vazou ate para "como e seu nome?".
    #
    # Duas causas somadas:
    #   1. a trava antiga olhava so a resposta IMEDIATAMENTE anterior; entre uma
    #      "como voce esta" e a outra havia turnos da webcam, entao ela nunca
    #      disparou;
    #   2. o cerebro e o Codex com esforco baixo — mesma pergunta e mesmo
    #      contexto dao a MESMA saida, deterministicamente. Nao adianta so
    #      conferir depois: o pedido tem de mudar.
    #
    # Conserto em duas partes: a NOTA passa a listar o que ele acabou de dizer
    # e proibir repetir (o pedido muda a cada turno, o que ja quebra o
    # determinismo), e a conferencia de saida varre as ULTIMAS DEZ falas.
    ECOS_OLHADOS = 10

    def _falas_recentes(self, quantas: int = ECOS_OLHADOS) -> list:
        """As ultimas respostas dele, da mais nova para a mais velha."""
        ditas = []
        for _m in reversed(self.historico):
            if _m.get("role") != "assistant":
                continue
            _t = str(_m.get("content") or "").strip()
            if _t:
                ditas.append(_t)
            if len(ditas) >= quantas:
                break
        return ditas

    def _ja_disse_assim(self, saida: str, quantas: int = ECOS_OLHADOS) -> str:
        """Devolve a fala antiga que esta sendo copiada, ou "" se for nova."""
        if not saida:
            return ""
        for _a in self._falas_recentes(quantas):
            if self._e_o_mesmo_comeco(_a, saida):
                return _a
        return ""

    def _nota_de_variedade(self) -> str:
        """A linha da nota que proibe repetir. Refeita a cada turno."""
        ditas = self._falas_recentes(6)
        if not ditas:
            return ""
        amostra = " / ".join(f'"{d[:70]}"' for d in ditas)
        return ("VARIE — esta e a regra que ele mais cobra. Voce JA disse, "
                "nesta conversa: " + amostra + ". E PROIBIDO comecar ou "
                "responder de novo com qualquer uma dessas formas, mesmo que "
                "a pergunta seja identica a de antes. Diga a mesma ideia com "
                "OUTRAS palavras, outra abertura, outro humor. Gente nunca "
                "responde duas vezes igual.")

    @staticmethod
    def _e_o_mesmo_comeco(a: str, b: str, palavras: int = 8) -> bool:
        """Duas falas comecam igual? Compara so o comeco, sem pontuacao.

        Oito palavras: e o bastante para nao confundir duas respostas que por
        acaso comecam com "pronto, chefe", e curto o bastante para pegar a
        copia mesmo quando o modelo muda o fim da frase.
        """
        import re as _re  # noqa: PLC0415
        def _limpo(s):
            s = _re.sub(r"[^\w\s]", " ", str(s or "").lower())
            return _re.sub(r"\s+", " ", s).strip().split()
        pa, pb = _limpo(a)[:palavras], _limpo(b)[:palavras]
        return len(pa) >= 4 and pa == pb

    def perguntar(self, texto: str, timeout: float = 60.0) -> str:
        """Pergunta ao modelo, executando as ferramentas que ele pedir.

        O laco tem teto de 3 rodadas de propósito: sem ele, um modelo confuso
        fica pedindo ferramenta para sempre e o cliente espera calado.
        """
        texto = str(texto or "").strip()
        if not texto:
            return ""
        # MONTAR O PROMPT NA HORA, nao usar o guardado.
        #
        # 01/09/2026: o Diego preencheu os dados dele na engrenagem e o JARVIS
        # continuou dizendo "nao tenho essa informacao". O motivo: o
        # `self.sistema` era montado UMA vez, na criacao do cerebro, e o que ele
        # digitou depois nunca entrava. Remontar custa juntar algumas strings —
        # nada perto de responder errado sobre quem o dono e.
        try:
            self.sistema = self._prompt_da_personalidade()
        except Exception:
            pass
        mensagens = [{"role": "system", "content": self.sistema}]
        mensagens.extend(self.historico[-MEMORIA_MAX:])
        # O RELOGIO E O JEITO VAO DENTRO DA PROPRIA PERGUNTA (05/09/2026).
        #
        # Eles iam como DUAS MENSAGENS SOLTAS do dono, logo antes da pergunta
        # de verdade. Para o modelo eram tres falas seguidas — e ele respondia
        # as tres: TODA resposta terminava com "Que horas sao? Sabado, cinco de
        # setembro de dois mil e vinte e seis, tres da manha", que ninguem
        # tinha perguntado. Uma vez ele chegou a DEVOLVER a nota como se fosse
        # fala dele ("Hmm, que horas sao agora?").
        #
        # Agora e UMA mensagem so: a nota entre colchetes, com a ordem de nao
        # responder a ela, e o pedido do dono embaixo. Nao sobra turno solto
        # pedindo resposta, e o motivo de estarem aqui embaixo (e nao no prompt
        # de sistema) continua valendo:
        #   - o prefixo cacheado nao muda a cada minuto (99% de acerto);
        #   - a hora nunca envelhece, porque a linha e refeita a cada pergunta;
        #   - modelo fraco obedece o que leu por ULTIMO, e o jeito precisa
        #     estar perto (medido em 03/09: com o jeito so no sistema,
        #     "ignorante", "brincalhao" e "mordomo" saiam os tres iguais).
        # Nada disto vai para o `historico`.
        nota = ["[NOTA INTERNA DO SISTEMA — nao responda a esta nota, nao a "
                "repita e nao a comente. Ela existe so para voce se situar.",
                _linha_do_relogio()]
        _jeito = self._lembrete_do_jeito()
        if _jeito:
            nota.append(_jeito)
        # O QUE ELE JA DISSE, para nao dizer de novo. Ver `_nota_de_variedade`.
        _variar = self._nota_de_variedade()
        if _variar:
            nota.append(_variar)
        # DUAS ACOES NA FRASE? AVISA QUE NAO PODE PARAR NA PRIMEIRA.
        if _PEDIDO_ENCADEADO.search(texto or ""):
            nota.append(
                "ATENCAO: o pedido abaixo tem MAIS DE UMA acao. Execute TODAS, "
                "uma ferramenta por vez, na ordem em que ele pediu. "
                "O QUE CONTA COMO TERMINADO e a ULTIMA acao da frase, nao a "
                "primeira: em 'abre o bloco de notas E DIGITA um script', "
                "terminar e ter DIGITADO — abrir foi so o comeco. "
                "ARMADILHA: quando a ferramenta responde que a coisa JA ESTAVA "
                "PRONTA (o programa ja aberto, o arquivo ja existe), isso NAO e "
                "o fim da tarefa, e so um passo que saiu de graca — chame a "
                "proxima ferramenta na mesma hora, sem falar com o dono. "
                "Responder no meio, ou repetir a frase que a ferramenta "
                "devolveu, conta como tarefa NAO cumprida.")
        nota.append("Fim da nota. Responda APENAS a mensagem do dono, abaixo.]")
        mensagens.append({"role": "user",
                          "content": "\n".join(nota) + "\n\n" + texto})

        # Pedido de ACAO? Guardado para conferir depois se ele realmente agiu.
        _pediu_acao = _parece_pedido_de_acao(texto)
        _chamou_ferramenta = False

        saida = ""

        # CEREBRO RAPIDO PRIMEIRO (31/08/2026).
        #
        # Medido lado a lado, mesma pergunta, mesmas 30 ferramentas:
        #   OpenRouter :free ... 10 a 16 s     Gemini flash-lite ... 0,7 a 1,9 s
        # E naquele momento SEIS dos sete modelos gratuitos estavam em 429; a
        # fila inteira era um modelo so. O Diego sentiu isso como 68 s entre
        # falar e o JARVIS mexer um dedo.
        #
        # Se a Gemini falhar (chave, rede, cota), `_pensar_com_gemini` devolve
        # None sem levantar excecao e o laco dos gratuitos assume normalmente.
        # A ESCADA INTEIRA NUMA CHAMADA (04/09/2026).
        #
        # Aqui existia um laco de tres rodadas que percorria a fila de modelos
        # gratuitos do OpenRouter, chamava a API na mao e executava as
        # ferramentas que viessem. Com o OpenRouter fora, sobrou o que sempre
        # importou: perguntar a escada (Gemini do cliente -> nossa OpenAI, se
        # houver -> Codex da VPS). Cada cerebro executa as proprias ferramentas
        # por dentro e devolve o mesmo dicionario.
        _r = self._pensar(mensagens)
        if _r is not None and (_r.get("texto") or _r.get("chamou_ferramenta")):
            saida = _r["texto"]
            _chamou_ferramenta = bool(_r["chamou_ferramenta"])

            # ── A SEGUNDA ACAO E COBRADA POR CODIGO (05/09/2026) ───────────
            #
            # "Abre o bloco de notas E DIGITA um script": ele abria, a
            # ferramenta respondia "ja esta aberto" e ele encerrava a tarefa
            # repetindo essa frase. Nada era digitado.
            #
            # Tentei resolver no prompt DUAS vezes, com aviso cada vez mais
            # explicito. Nao segurou — e a segunda tentativa piorou (os dois
            # casos do teste passaram a falhar). Instrucao e pedido; o que
            # garante e o programa. Aqui a cobranca e uma volta a MAIS, com
            # `forcar=True`, que so acontece quando as duas coisas sao verdade:
            # o pedido tinha mais de uma acao E ele parou depois da primeira.
            #
            # Barato: nao dispara em pergunta, em conversa, nem em pedido de
            # uma acao so — que sao a esmagadora maioria dos turnos.
            if _chamou_ferramenta and _PEDIDO_ENCADEADO.search(texto or ""):
                mensagens.append({"role": "assistant", "content": saida or ""})
                mensagens.append({"role": "user", "content": (
                    "PARE E OLHE O PEDIDO DE NOVO: ele tinha mais de uma acao e "
                    "voce executou so a primeira. O pedido do dono, palavra por "
                    "palavra, foi: >>> " + str(texto or "") + " <<<. "
                    "Faca AGORA a acao seguinte DESSE pedido, chamando a "
                    "ferramenta certa — e nao me responda antes de fazer. "
                    "OS ARGUMENTOS SAEM DO PEDIDO DELE, nao da sua cabeca: se "
                    "ele disse 'uma calculadora completa', e uma calculadora "
                    "completa; nao troque por um exemplo simples nem por um "
                    "'ola mundo'. Se a acao for escrever ou digitar, a "
                    "ferramenta e `digitar_na_tela` e o campo `pedido` tem de "
                    "descrever exatamente o que ele mandou escrever. Se ja "
                    "tiver feito TUDO, ai sim me diga o resultado final.")})
                _antes_da_cobranca = len(mensagens) - 2
                # DUAS TENTATIVAS, NAO UMA. A segunda so acontece quando a
                # primeira voltou sem chamar ferramenta nenhuma — tipicamente
                # com uma PROMESSA ("estou escrevendo o script..."), que e
                # exatamente a mentira que este projeto ja aprendeu a nao
                # aceitar. Custa ~1,5 s no caso raro.
                for _insistencia in (1, 2):
                    _r2 = self._pensar(mensagens, forcar=True)
                    _agiu = bool(_r2 and _r2.get("chamou_ferramenta"))
                    if _r2 is not None and (_r2.get("texto") or _agiu):
                        if _r2.get("texto"):
                            saida = _r2["texto"]
                        _chamou_ferramenta = _chamou_ferramenta or _agiu
                    if _agiu:
                        break
                    if _insistencia == 1:
                        mensagens.append({"role": "user", "content": (
                            "VOCE PROMETEU EM VEZ DE FAZER. Nao existe 'estou "
                            "escrevendo' nem 'ja vou fazer': ou voce chama a "
                            "ferramenta agora, nesta resposta, ou a tarefa fica "
                            "pela metade. Chame a ferramenta.")})
                        continue
                    break
                # Tira o que ESTA cobranca empilhou. Conta em vez de procurar
                # pelo conteudo: a mensagem do dono nao esta pura na lista (ela
                # vai colada na nota interna), entao comparar texto apagaria a
                # conversa inteira.
                del mensagens[_antes_da_cobranca:]
        elif not saida:
            # Nenhum degrau respondeu. O ultimo deles nao tem cota — se ele
            # falhou, e a ponte com a VPS que esta fora. Dizer isso e melhor do
            # que ficar calado, e MUITO melhor do que inventar uma resposta.
            saida = ("Nao consegui pensar agora: perdi a ligacao com o servidor. "
                     "Tenta de novo em instantes.")

        # TRAVA CONTRA MENTIRA (30/08/2026).
        #
        # A regra no texto do sistema nao basta, e o motivo e o historico: o
        # modelo ve as PROPRIAS respostas anteriores dizendo "fechado, parceiro"
        # sem ferramenta nenhuma, e repete o padrao. Ele aprende a mentir com
        # ele mesmo. O Diego pediu para fechar e salvar o bloco de notas, ouviu
        # "fechado" e "salvo", e nada tinha acontecido.
        #
        # Aqui o codigo confere: se o pedido era de ACAO e NENHUMA ferramenta
        # foi chamada, devolve o modelo para a mesa com a cobranca explicita.
        # Duas mentiras diferentes, a mesma cobranca: dizer que fez sem ter
        # chamado nada, e prometer que vai fazer depois. Nos dois casos o
        # trabalho nao aconteceu.
        _empurrou = _chamou_ferramenta and _promete_sem_cumprir(saida)
        if _pediu_acao and saida and (not _chamou_ferramenta or _empurrou):
            mensagens.append({"role": "assistant", "content": saida})
            mensagens.append({"role": "user", "content": (
                ("PARE. Voce prometeu fazer o resto DEPOIS ('ja volto', 'vou "
                 "abrir', 'um instante'). Nao existe depois: este turno acaba "
                 "quando voce parar de falar, e o dono nao vai receber nada. "
                 "Termine AGORA, nesta mesma resposta: chame a ferramenta que "
                 "falta e traga o resultado completo. "
                 if _empurrou else
                 "PARE. Voce respondeu como se tivesse feito, mas NAO chamou "
                 "ferramenta nenhuma — entao nada aconteceu de verdade. Chame "
                 "AGORA a ferramenta certa para executar o que foi pedido. "
                 "Se a pergunta era HORA, DATA, NOTICIA, COTACAO ou CLIMA, voce "
                 "NAO SABE a resposta: chame hora_agora ou web_search. O numero "
                 "que veio na sua cabeca esta errado. ") +
                # SEM ESTA FRASE a trava vira uma fabrica de recusa: em
                # 30/08/2026 o dono pediu "manda um salve pra rapaziadinha" e
                # ouviu "nao tenho ferramenta pra isso". Pedido de FALA se
                # cumpre falando.
                "MAS ATENCAO: se o que ele pediu e para VOCE FALAR alguma coisa "
                "(mandar um salve, dar um alo, contar uma piada, cantar, "
                "explicar algo), entao nao ha ferramenta nenhuma envolvida — "
                "e so FALAR, e voce ja pode fazer isso agora mesmo, com gosto. "
                "So diga que nao consegue quando for mexer no computador ou no "
                "mundo de fora e realmente faltar ferramenta.")})
            # COBRAR NO MESMO CEREBRO QUE RESPONDEU.
            #
            # Isto chamava `self._chamar`, que fala com o OpenRouter. Como ele
            # virou reserva e vive em 429, a cobranca morria no `except` e a
            # mentira ficava de pe — foi assim que "vou gerar a imagem, so um
            # instante" passou sem gerar nada (31/08/2026).
            # A COBRANCA VAI PARA A MESMA ESCADA, agora com o Codex no fim
            # dela. `forcar=True`: nesta volta ele NAO pode responder sem
            # chamar ferramenta nenhuma.
            _r2 = self._pensar(mensagens, forcar=True)
            if _r2 is not None and (_r2.get("texto") or _r2.get("chamou_ferramenta")):
                if _r2.get("texto"):
                    saida = _r2["texto"]
                _chamou_ferramenta = _chamou_ferramenta or bool(_r2["chamou_ferramenta"])
            mensagens = mensagens[:-2]
            return self._fechar_resposta(texto, saida)

        # TETO NO CODIGO, nao so no texto do sistema. O modelo entende "seja
        # curto" como sugestao e escapa: em 30/08 devolveu 17 segundos de fala,
        # e o dono esperou tudo isso ouvindo. Aqui corta de verdade, no fim da
        # ultima frase que couber.
        # ERA 45 e cortava explicacao no meio. O motivo do corte era a fala
        # demorar a sair; agora ela sai frase por frase e a primeira ja toca
        # enquanto o resto e gerado. 90 continua barrando discurso de verdade
        # sem mutilar uma resposta que o dono PEDIU longa.
        saida = self._limpar_para_voz(saida)
        if saida and len(saida.split()) > 45:
            import re as _re  # noqa: PLC0415
            frases = _re.split(r"(?<=[.!?])\s+", saida)
            cortada, total = [], 0
            for f in frases:
                n = len(f.split())
                if cortada and total + n > 45:
                    break
                cortada.append(f)
                total += n
            nova = " ".join(cortada).strip()
            if nova:
                saida = nova

        if saida:
            # So guarda o que deu certo: turno quebrado no historico envenena
            # todas as perguntas seguintes. As idas e voltas de ferramenta NAO
            # entram — o que importa para a proxima pergunta e o que foi dito.
            self.historico.append({"role": "user", "content": texto})
            self.historico.append({"role": "assistant", "content": saida})
            del self.historico[:-MEMORIA_MAX]
        return saida
