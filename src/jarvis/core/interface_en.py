"""Janela e chat do JARVIS em INGLES (26/09/2026).

Pedido do Diego para o cliente do Canada: o JARVIS ja FALAVA ingles (ver
core/idioma.py), mas o que ele ESCREVIA no chat continuava em portugues —
"[usar_computador] 3 min, passo 16, ainda trabalhando", "Voce: ...", "LOCAL:
ouvir 0.7s | pensar 1.3s" — e a janela inteira tambem ("COMANDOS RAPIDOS",
"Digite um comando ou pergunta...", "MICROFONE ATIVO").

Em vez de mexer em ~150 mensagens espalhadas pelo programa, a traducao fica
AQUI, num ponto so:
  * `traduzir_linha`  -> toda linha que vai para o chat (ui.py, _translate_log_text);
  * `traduzir_texto`  -> todo texto de botao/rotulo/dica, e o que e DESENHADO
                         com QPainter (o status "OUVINDO" no meio da tela).
`instalar()` so e chamado com o idioma "en"; no portugues nada disto roda.
"""
from __future__ import annotations

import re
import unicodedata

# ── chat ────────────────────────────────────────────────────────────────────
# Molde portugues -> molde ingles. `{}` e o pedaco variavel (numero, nome,
# erro), copiado na mesma ordem. Casado do comeco ao fim da linha.
_CHAT = [
    ("Voce: {}", "You: {}"),
    ("Você: {}", "You: {}"),
    ("LOCAL: ouvir {}s | pensar {}s | voz {}s  (falando {}s)",
     "LOCAL: hear {}s | think {}s | voice {}s  (speaking {}s)"),
    ("LOCAL: o cerebro rapido recusou; indo pelo servidor.",
     "LOCAL: the fast brain refused; going through the server."),
    ("LOCAL: o cerebro devolveu vazio.", "LOCAL: the brain returned nothing."),
    ("LOCAL: falhou — {}: {}", "LOCAL: failed — {}: {}"),
    ("LOCAL: falha ao preparar — {}", "LOCAL: failed to prepare — {}"),
    ("LOCAL: sem sessao para pensar — repita em instantes.",
     "LOCAL: no session to think — please repeat in a moment."),
    ("LOCAL: modo sem Gemini esta {}.", "LOCAL: no-Gemini mode is {}."),
    ("LOCAL: desligado — voltou para a Gemini.", "LOCAL: off — back to Gemini."),
    ("VOZ LOCAL ({}): {}s de fala gerados em {}s", "LOCAL VOICE ({}): {}s of speech made in {}s"),
    ("VOZ LOCAL: falhou — {}: {}", "LOCAL VOICE: failed — {}: {}"),
    ("CODEX: perguntando — {}", "CODEX: asking — {}"),
    ("CODEX: falhou — {}: {}", "CODEX: failed — {}: {}"),
    ("CEREBRO: agora e {} — {}", "BRAIN: now it is {} — {}"),
    ("CEREBRO: memoria da conversa limpa.", "BRAIN: conversation memory cleared."),
    ("CEREBRO: {}", "BRAIN: {}"),
    ("VOZ: agora falo com '{}'.", "VOICE: now I speak with '{}'."),
    ("VOZ: agora e {}.", "VOICE: now it is {}."),
    ("VOZ: troquei, mas nao consegui tocar a amostra agora. Fale comigo que voce ja ouve a voz nova.",
     "VOICE: changed, but I couldn't play the sample now. Talk to me and you'll hear the new voice."),
    ("VOZES: {}", "VOICES: {}"),
    ("JEITO: agora sou {}.", "STYLE: now I am {}."),
    ("SOM: toquei um bip nesse aparelho. Ouviu? Entao e esse. Se nao ouviu, tente o proximo da lista.",
     "SOUND: I played a beep on this device. Did you hear it? Then this is the one. "
     "If not, try the next one on the list."),
    ("SOM: esse aparelho recusou ({}).", "SOUND: this device refused ({})."),
    ("MICROFONE: trocado — {} blocos recebidos, funcionando.",
     "MICROPHONE: changed — {} blocks received, working."),
    ("MICROFONE: esse nao respondeu ({}). Escolha outro ou feche programas que usam o microfone.",
     "MICROPHONE: this one didn't respond ({}). Pick another one or close programs using the mic."),
    ("AVISO: microfone indisponível; tentando novamente sem interromper o JARVIS.",
     "WARNING: microphone unavailable; trying again without stopping JARVIS."),
    ("AVISO: saída de áudio indisponível; tentando novamente sem interromper o JARVIS.",
     "WARNING: audio output unavailable; trying again without stopping JARVIS."),
    ("ATENÇÃO: o limite de uso da sua chave de IA acabou. O JARVIS volta a falar assim que o "
     "limite for renovado ou o plano da chave for ajustado.",
     "ATTENTION: your AI key's usage limit ran out. JARVIS will talk again as soon as the limit "
     "renews or the key's plan is adjusted."),
    ("REDE: Sem conexão — tentando de novo em {}s.", "NETWORK: No connection — trying again in {}s."),
    ("ERR: nao consegui trocar — {}: {}", "ERR: couldn't change — {}: {}"),
    ("ERR: transcrição literal divergente — {}", "ERR: literal transcription differs — {}"),
    ("ERR: confirmação de voz literal — {}", "ERR: literal voice confirmation — {}"),
    ("ERR: nao consegui abrir as vozes — {}", "ERR: couldn't open the voices — {}"),
    ("ERR: configuracoes falharam — {}: {}", "ERR: settings failed — {}: {}"),
    ("ERR: nao abri as chaves — {}", "ERR: couldn't open the keys — {}"),
    ("SIS: Repetindo seu pedido — o servidor não respondeu.",
     "SYS: Repeating your request — the server didn't answer."),
    ("SIS: Comecei a editar a imagem.", "SYS: I started editing the image."),
    ("SIS: parando o computer use — e nao vou comecar outro.",
     "SYS: stopping computer use — and I won't start another."),
    ("SIS: computer use ATIVADO — esperando a tarefa.", "SYS: computer use ON — waiting for the task."),
    ("SIS: computer use desativado antes de comecar.", "SYS: computer use turned off before starting."),
    ("SIS: nao entendi a tarefa ({}); continuo esperando.",
     "SYS: I didn't understand the task ({}); still waiting."),
    ("SIS: desisti de esperar a tarefa; o computador segue livre.",
     "SYS: stopped waiting for the task; the computer is free."),
    ("SIS: parando o que estava fazendo na tela.", "SYS: stopping what I was doing on the screen."),
    ("SIS: correcao no meio do caminho: {}", "SYS: correction on the way: {}"),
    ("SIS: Destravando a conversa — um instante, sem perder nada.",
     "SYS: Unfreezing the conversation — one moment, nothing lost."),
    ("SIS: Ainda me reconectando — repita em instantes.",
     "SYS: Still reconnecting — please repeat in a moment."),
    ("SIS: Controle remoto indisponivel: {}", "SYS: Remote control unavailable: {}"),
    ("SIS: Reiniciando o JARVIS sozinho em {}s...", "SYS: Restarting JARVIS by itself in {}s..."),
    ("SIS: JARVIS reiniciado.", "SYS: JARVIS restarted."),
    ("SIS: voz indisponivel; falando com a voz local.", "SYS: voice unavailable; speaking with the local voice."),
    ("SIS: a voz engasgou neste turno; a resposta ficou escrita.",
     "SYS: the voice stalled this turn; the answer is written here."),
    ("SIS: Microfone de volta.", "SYS: Microphone is back."),
    ("SIS: religando a Gemini...", "SYS: reconnecting Gemini..."),
    ("SIS: Recursos avançados de áudio indisponíveis — reconectando sem eles.",
     "SYS: Advanced audio features unavailable — reconnecting without them."),
    ("SIS: Reiniciando o mecanismo de voz...", "SYS: Restarting the voice engine..."),
    ("SIS: Saída de áudio disponível novamente.", "SYS: Audio output available again."),
    ("SIS: tirando o print e marcando pra voce...", "SYS: taking a screenshot and marking it for you..."),
    ("SIS: pedido de desligar sem ordem clara — perguntei antes.",
     "SYS: shutdown request without a clear order — I asked first."),
    ("SIS: Abrindo conexão do WhatsApp...", "SYS: Opening the WhatsApp connection..."),
    ("SIS: Atalho na área de trabalho criado.", "SYS: Desktop shortcut created."),
    ("SIS: Inicializado. SO={}. JARVIS online.", "SYS: Started. OS={}. JARVIS online."),
    ("SIS: Microfone ativo.", "SYS: Microphone on."),
    ("SIS: Microfone silenciado.", "SYS: Microphone muted."),
    ("SIS: Módulo WhatsApp indisponível.", "SYS: WhatsApp module unavailable."),
    ("SIS: Não foi possível gerar a chave remota.", "SYS: Couldn't generate the remote key."),
    ("SIS: Painel remoto não está rodando — remoto indisponível.",
     "SYS: Remote panel isn't running — remote unavailable."),
    ("SIS: WhatsApp já está iniciando. Aguarde o QR Code.",
     "SYS: WhatsApp is already starting. Wait for the QR code."),
    ("SIS: agora ele te chama de {}.", "SYS: now he calls you {}."),
    ("SIS: agora eu so respondo se voce me chamar de {}. {}",
     "SYS: now I only answer if you call me {}. {}"),
    ("SIS: escolha registrada, mas o motor nao respondeu.",
     "SYS: choice saved, but the engine didn't respond."),
    ("SIS: voltei a responder sem precisar do nome.", "SYS: I answer again without needing the name."),
    ("SIS: O Windows trocou o aparelho de som — reabrindo.{}", "SYS: The sound device changed — reopening.{}"),
    ("SIS: Interrompido — ouvindo...", "SYS: Interrupted — listening..."),
    ("SIS: JARVIS ativo.", "SYS: JARVIS online."),
    ("SYS: fala duplicada suprimida.", "SYS: duplicated speech suppressed."),
    ("ERRO: Falha ao abrir painel WhatsApp — {}", "ERR: Failed to open the WhatsApp panel — {}"),
    ("ERRO: Falha ao criar atalho — {}", "ERR: Failed to create the shortcut — {}"),
    ("ARQ: {} ({}) carregado", "FILE: {} ({}) loaded"),
    ("[Celular]: {}", "[Phone]: {}"),
    ("[comando] {}", "[command] {}"),
    ("[file] procurando {}", "[file] searching {}"),
    ("[file] aguardando confirmação para excluir: {}", "[file] waiting for confirmation to delete: {}"),
    ("[file] confirmação recebida; excluindo: {}", "[file] confirmation received; deleting: {}"),
    ("[file] confirmacao recebida; excluindo: {}", "[file] confirmation received; deleting: {}"),
    ("[file] exclusão cancelada: {}", "[file] deletion cancelled: {}"),
    ("[open_app] abrindo caminho: {}", "[open_app] opening path: {}"),
    ("[WhatsApp] Abrindo QR Code/conexão...", "[WhatsApp] Opening QR code/connection..."),
    ("[WhatsApp] erro ao abrir conexão: {}", "[WhatsApp] error opening the connection: {}"),
    ("[WhatsApp agendamento] erro: {}", "[WhatsApp schedule] error: {}"),
    ("[WhatsApp agendamento] {}", "[WhatsApp schedule] {}"),
    ("[WhatsApp agente] erro: {}", "[WhatsApp agent] error: {}"),
    ("[WhatsApp agente] Respondeu para {}: {}", "[WhatsApp agent] Replied to {}: {}"),
    ("[usar_computador] {} min, passo {}, ainda trabalhando",
     "[computer] {} min, step {}, still working"),
    ("[usar_computador] fim em {}s, {} anotacoes", "[computer] finished in {}s, {} notes"),
    ("[usar_computador] passo {}: {} movimento(s) em {}s{}", "[computer] step {}: {} move(s) in {}s{}"),
    ("[usar_computador] olhar falhou: {}", "[computer] looking failed: {}"),
    ("[usar_computador] correcao do dono: {}", "[computer] owner's correction: {}"),
    ("[usar_computador] o dono perguntou no meio: {}", "[computer] the owner asked in the middle: {}"),
    ("[usar_computador] conclusao sem prova: {}", "[computer] conclusion without proof: {}"),
    ("[usar_computador] esperei o aviso terminar ({}s)", "[computer] waited for the notice to end ({}s)"),
    ("[usar_computador] mudou: {}", "[computer] changed: {}"),
    ("[usar_computador]   viu: {} | {}", "[computer]   saw: {} | {}"),
    ("[digitar] {} caracteres em {}s", "[type] {} characters in {}s"),
    ("[digitar] conteudo gerado em {}s, {} caracteres", "[type] content made in {}s, {} characters"),
    ("[digitar] nao consegui trazer {} para a frente; digitando assim mesmo",
     "[type] couldn't bring {} to the front; typing anyway"),
    ("[desenhar] troquei o computer use pelo desenhista", "[draw] switched computer use to the drawer"),
    ("[desenhar] o Codex nao olhou: {}", "[draw] Codex didn't look: {}"),
    ("[Computer] destino Salvar como corrigido: {}", "[Computer] Save As destination fixed: {}"),
    ("[Computer] não consegui corrigir Salvar como: {}", "[Computer] couldn't fix Save As: {}"),
    ("Deixa eu olhar aqui, chefe.", "Let me take a look, boss."),
    ("Webcam aberta, chefe.", "Webcam open, boss."),
    ("Fechei a webcam, chefe.", "Webcam closed, boss."),
    ("Nao consegui abrir a webcam.", "I couldn't open the webcam."),
    ("Nao consegui fechar a webcam.", "I couldn't close the webcam."),
    ("Nao consegui abrir o {}.", "I couldn't open {}."),
    ("Abri o {}, chefe.", "I opened {}, boss."),
    ("Fechei a pasta {}, chefe.", "I closed the {} folder, boss."),
    ("A pasta {} esta aberta, mas nao quis fechar, chefe.",
     "The {} folder is open, but it wouldn't close, boss."),
    ("A pasta {} nao esta aberta, chefe.", "The {} folder isn't open, boss."),
    ("Ainda estou montando o site anterior, chefe. Assim que ficar pronto eu te aviso.",
     "I'm still building the previous site, boss. I'll let you know as soon as it's ready."),
    ("Status: conectado com sucesso{}. Já pode fechar esta janela.",
     "Status: connected successfully{}. You can close this window."),
    ("Status: escaneie o QR Code para conectar.", "Status: scan the QR code to connect."),
    ("Status: iniciando ponte WhatsApp...", "Status: starting the WhatsApp bridge..."),
    ("Status: iniciando ponte local. Aguarde alguns segundos.",
     "Status: starting the local bridge. Wait a few seconds."),
    ("Status: sessão limpa. Gerando QR Code novo...", "Status: session cleared. Making a new QR code..."),
    ("Status: {}{} | veja whatsapp_bridge\\bridge.log se continuar.",
     "Status: {}{} | see whatsapp_bridge/bridge.log if it continues."),
]

# prefixos que sobram (quando a linha nao casou com nenhum molde inteiro)
_PREFIXOS = [
    ("SIS:", "SYS:"), ("Voce:", "You:"), ("Você:", "You:"), ("ERRO:", "ERR:"), ("ARQ:", "FILE:"),
    ("CEREBRO:", "BRAIN:"), ("VOZ LOCAL", "LOCAL VOICE"), ("VOZ:", "VOICE:"), ("JEITO:", "STYLE:"),
    ("SOM:", "SOUND:"), ("MICROFONE:", "MICROPHONE:"), ("REDE:", "NETWORK:"), ("AVISO:", "WARNING:"),
    ("ATENÇÃO:", "ATTENTION:"), ("[usar_computador]", "[computer]"), ("[desenhar]", "[draw]"),
    ("[digitar]", "[type]"), ("[organizar_pasta]", "[organize_folder]"), ("[comando]", "[command]"),
    ("[desfazer]", "[undo]"), ("[Celular]", "[Phone]"), ("[marcar]", "[mark]"),
]

# Palavras soltas das linhas tecnicas ([computer], [draw]...) que nao tem molde
# proprio. Traducao palavra a palavra: fica tecnico, mas legivel.
_GLOSSARIO = {
    "passo": "step", "passos": "steps", "ainda": "still", "trabalhando": "working",
    "falhou": "failed", "nao": "not", "não": "not", "consegui": "managed", "olhar": "look",
    "olhei": "looked", "conferencia": "check", "pedido": "request", "tarefa": "task",
    "fim": "end", "em": "in", "de": "of", "e": "and", "o": "the", "a": "the", "os": "the",
    "as": "the", "do": "of the", "da": "of the", "no": "in the", "na": "in the",
    "com": "with", "sem": "without", "para": "for", "por": "by", "que": "that",
    "anotacoes": "notes", "fechei": "closed", "abri": "opened", "caixa(s)": "box(es)",
    "erro": "error", "janela": "window", "janelas": "windows", "dono": "owner",
    "pergunta": "question", "respondi": "answered", "sozinho": "by myself", "receita": "recipe",
    "receitas": "recipes", "fora": "off", "caminhos": "paths", "montei": "built",
    "mudou": "changed", "viu": "saw", "bloqueei": "blocked", "clique": "click",
    "contra": "against", "investiguei": "investigated", "antes": "before", "falta": "missing",
    "conserto": "fix", "manual": "manual", "movimento(s)": "move(s)", "esperei": "waited",
    "aviso": "notice", "terminar": "finish", "correcao": "correction", "perguntou": "asked",
    "meio": "middle", "conclusao": "conclusion", "prova": "proof", "folha": "sheet",
    "esticando": "stretching", "caber": "fit", "ferramenta": "tool", "achei": "found",
    "cursor": "cursor", "tamanho": "size", "aceitou": "accepted", "segunda": "second",
    "passada": "pass", "veio": "came", "mais": "more", "pobre": "poor", "pecas": "pieces",
    "formas": "shapes", "estourei": "ran out of", "tempo": "time", "pulei": "skipped",
    "colei": "pasted", "imagem": "image", "desenho": "drawing", "veredito": "verdict",
    "prosa": "prose", "manchas": "spots", "cor": "color", "tiradas": "taken", "foto": "photo",
    "total": "total", "contorno": "outline", "referencia": "reference", "editor": "editor",
    "sigo": "going on", "assim": "like this", "mesmo": "anyway", "avisar": "warn",
    "freio": "brake", "ler": "read", "cerebro": "brain", "plano": "plan", "tinha": "had",
    "narrador": "narrator", "acessibilidade": "accessibility", "ar": "air",
    "caracteres": "characters", "conteudo": "content", "gerado": "made", "digitando": "typing",
    "trazer": "bring", "frente": "front", "ponte": "bridge", "devolveu": "returned",
    "um": "one", "uma": "one", "vez": "time", "regras": "rules", "campo": "field",
    "proprio": "own", "caderno": "notebook", "ligado": "on", "interruptor": "switch",
    "anotou": "noted", "falar": "speaking", "autorizada": "authorized", "previa": "prior",
    "montando": "building", "minutos": "minutes", "min": "min",
}
_PALAVRA = re.compile(r"[A-Za-zÀ-ÿ()]+")

_MOLDES = []
for _pt, _en in _CHAT:
    _partes = [re.escape(p) for p in _pt.split("{}")]
    _MOLDES.append((re.compile("^" + "(.*?)".join(_partes) + "$", re.S), _en))


def _preencher(molde: str, grupos) -> str:
    saida = molde
    for g in grupos:
        saida = saida.replace("{}", g, 1)
    return saida


def traduzir_linha(texto: str) -> str:
    """Uma linha do chat, em ingles. O que nao reconhece passa como veio."""
    if not isinstance(texto, str) or not texto:
        return texto
    # O que o JARVIS disse ja sai em ingles; e o que o dono disse fica como ele disse.
    if texto.startswith(("JARVIS:", "You:")):
        return texto
    for rx, en in _MOLDES:
        m = rx.match(texto)
        if m:
            return _preencher(en, m.groups())
    for pt, en in _PREFIXOS:
        if texto.startswith(pt):
            resto = texto[len(pt):]
            if pt.startswith("["):
                resto = _PALAVRA.sub(lambda mm: _GLOSSARIO.get(mm.group(0).lower(), mm.group(0)),
                                     resto)
            return en + resto
    return texto


# ── janela ──────────────────────────────────────────────────────────────────
# Frase da interface (sem acento, MAIUSCULA, espacos simples) -> ingles.
_UI = {
    "OUVINDO": "LISTENING", "FALANDO": "SPEAKING", "PENSANDO": "THINKING",
    "VOCE": "YOU", "SEM PASTA": "NO FOLDER", "TRABALHANDO": "WORKING",
    "PASTA": "FOLDER", "RODAR": "RUN", "DESFAZER": "UNDO", "PARAR": "STOP", "ABRIR": "OPEN",
    "PRONTO PARA TRABALHAR": "READY TO WORK",
    "PECA AO COWORK… (EX.: FAZ UM JOGO DA COBRINHA)": "Ask Cowork… (e.g.: make a snake game)", "SISTEMA": "SYSTEM", "CONVERSA": "CONVERSATION", "ARQUIVO": "FILE",
    "ARRASTE AQUI": "DROP HERE", "SOLTE": "DROP", "PARA ENVIAR": "TO SEND",
    "NENHUM ARQUIVO CARREGADO — CLIQUE OU ARRASTE NO BOTAO ARQUIVO":
        "No file loaded — click or drag onto the FILE button",
    "PROCESSANDO": "PROCESSING", "INICIANDO": "STARTING", "MICROFONE MUDO": "MIC MUTED",
    "MUDO": "MUTED", "INATIVO": "INACTIVE", "INATIVA": "INACTIVE", "ATIVO": "ON",
    "ATIVA": "ON", "DESLIGADA": "OFF", "DESLIGADO": "OFF", "CONECTADO": "CONNECTED",
    "MICROFONE ATIVO": "MICROPHONE ON", "SEMPRE OUVINDO": "ALWAYS LISTENING",
    "SO COM O NOME": "ONLY BY NAME", "ACORDADO 30S": "AWAKE 30s",
    "INTERROMPER [ESC]": "INTERRUPT [ESC]", "INTERROMPER": "INTERRUPT",
    "CONECTAR WHATSAPP": "CONNECT WHATSAPP", "WHATSAPP CONECTADO": "WHATSAPP CONNECTED",
    "CONTROLE REMOTO": "REMOTE CONTROL", "WEBCAM INATIVA": "WEBCAM OFF",
    "WEBCAM ATIVA": "WEBCAM ON", "ENVIAR ARQUIVO": "SEND FILE",
    "CLIQUE OU ARRASTE": "CLICK OR DRAG", "COMANDOS RAPIDOS": "QUICK COMMANDS",
    "TRANSCRICAO / STATUS": "TRANSCRIPT / STATUS", "PAINEL EXPANSIVEL": "EXPANDABLE PANEL",
    "DIGITE UM COMANDO OU PERGUNTA…": "Type a command or question…",
    "DIGITE UM COMANDO OU PERGUNTA...": "Type a command or question...",
    "NENHUM ARQUIVO CARREGADO — CLIQUE OU ARRASTE NO CARTAO ENVIAR ARQUIVO":
        "No file loaded — click or drag onto the SEND FILE card",
    "[F4] MUDO · [F11] TELA CHEIA · [F2] CHAVE DE IA":
        "[F4] Mute · [F11] Full screen · [F2] AI key",
    "CAMERA": "CAMERA", "◈ CAMERA": "◈ CAMERA", "VISAO DESL.": "VISION OFF",
    "NUCLEO ATIVO": "CORE ONLINE", "SEGURANCA OK": "SECURITY OK",
    "PROTOCOLO JARVIS": "JARVIS PROTOCOL", "REDE": "NET", "LIG": "UP", "SO": "OS",
    "SO LINUX": "OS LINUX", "SISTEMA OPERACIONAL": "OPERATING SYSTEM",
    "JARVIS INDUSTRIES · CONFIDENCIAL": "JARVIS Industries · CONFIDENTIAL",
    "PONTE WHATSAPP INICIANDO": "WHATSAPP BRIDGE STARTING",
    "QR CODE AINDA NAO GERADO": "QR CODE NOT MADE YET",
    "GERANDO QR CODE NOVO": "MAKING NEW QR CODE", "GERAR QR NOVO": "NEW QR CODE",
    "GERANDO QR CODE... AGUARDE ATE 30 SEGUNDOS.": "Making the QR code... wait up to 30 seconds.",
    "ABRA O WHATSAPP NO CELULAR → APARELHOS CONECTADOS → CONECTAR APARELHO.":
        "Open WhatsApp on your phone → Linked devices → Link a device.",
    "APONTE A CAMERA DO CELULAR PARA CONECTAR": "Point your phone camera to connect",
    "CELULAR CONECTADO — JARVIS PRONTO": "Phone connected — JARVIS ready",
    "ABRINDO WEBCAM": "OPENING WEBCAM", "AGUARDANDO CAMERA…": "WAITING FOR CAMERA…",
    "AO VIVO": "LIVE", "FECHAR": "CLOSE", "FECHAR ✕": "CLOSE ✕", "✕ FECHAR": "✕ CLOSE",
    "ATUALIZAR": "REFRESH", "TESTAR TODOS": "TEST ALL",
    "TESTANDO CADA APARELHO, AGUARDE...": "Testing each device, please wait...",
    "CLIQUE EM TESTAR PARA VER QUAL FUNCIONA.": "Click Test to see which one works.",
    "CLIQUE PARA OUVIR UM BIP E ADOTAR.": "Click to hear a beep and use it.",
    "ESCOLHA UM QUE ESTEJA MARCADO COM ✓.": "Pick one marked with ✓.",
    "▸ INICIAR SISTEMAS": "▸ START SYSTEMS", "◈ ACESSO REMOTO": "◈ REMOTE ACCESS",
    "◈ CONFIGURACAO": "◈ SETTINGS", "◈ ENTRADA VISUAL": "◈ VISUAL INPUT",
    "◉ CONECTAR WHATSAPP": "◉ CONNECT WHATSAPP", "CONECTAR WHATSAPP (MINUSCULO)": "",
    "CHAVE": "KEY", "CHAVE API GEMINI": "GEMINI API KEY", "NOVA CHAVE": "NEW KEY",
    "ABRIR O CADASTRO DE CHAVES": "OPEN THE KEYS PAGE",
    "A CHAVE DO GEMINI E OPCIONAL: DEIXA O JARVIS MAIS RAPIDO.":
        "The Gemini key is optional: it makes JARVIS faster.",
    "VOCE PODE COMECAR SEM CHAVE NENHUMA.": "You can start without any key.",
    "OU DIGITE MANUALMENTE:": "Or type it manually:",
    "VOZ E JEITO": "VOICE AND STYLE", "JARVIS · VOZ E JEITO DE SER": "JARVIS · Voice and Personality",
    "VOZ · COMO ELE SOA": "VOICE · how he sounds", "JEITO · COMO ELE AGE": "STYLE · how he acts",
    "MICROFONE · POR ONDE ELE OUVE": "MICROPHONE · where he listens",
    "SOM · POR ONDE ELE FALA": "SOUND · where he speaks", "MICROFONE E SOM": "MICROPHONE AND SOUND",
    "SAUDACAO AO ABRIR · ELE TE CUMPRIMENTA QUANDO O PROGRAMA ABRE":
        "GREETING ON START · he greets you when the program opens",
    "SOBRE VOCE": "ABOUT YOU", "SOBRE VOCE · PARA ELE TE CONHECER": "ABOUT YOU · so he knows you",
    "RESUMO": "SUMMARY",
    "CONFIGURAR: CHAVE, VOZ, JEITO, MICROFONE, SOM E SEUS DADOS":
        "Settings: key, voice, style, microphone, sound and your details",
    "SELECIONAR ARQUIVO PARA O JARVIS": "Select a file for JARVIS",
    "ARRASTE O ARQUIVO AQUI OU CLIQUE PARA PROCURAR": "Drag the file here or click to browse",
    "SOLTE PARA CARREGAR": "Drop to load",
    "IMAGENS · VIDEO · AUDIO · PDF · DOCS · CODIGO · DADOS":
        "Images · Video · Audio · PDF · Docs · Code · Data",
    "JARVIS ESTA USANDO O COMPUTADOR · ESC INTERROMPE · NAO MEXA NO MOUSE":
        "JARVIS IS USING THE COMPUTER · ESC stops · don't touch the mouse",
    "STATUS: ESCANEIE O QR CODE PARA CONECTAR.": "Status: scan the QR code to connect.",
    "STATUS: INICIANDO PONTE WHATSAPP...": "Status: starting the WhatsApp bridge...",
}


def _chave(t: str) -> str:
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", t).strip().upper()


def _mesma_caixa(original: str, en: str) -> str:
    letras = [c for c in original if c.isalpha()]
    if letras and all(c.isupper() for c in letras) and en.upper() != en:
        # o portugues estava TODO em maiuscula: o ingles tambem, menos frase longa
        return en.upper() if len(en) <= 40 else en
    return en


_CACHE: dict = {}
_UI_N: dict = {}
_TEM_LETRA = re.compile(r"[A-Za-zÀ-ÿ]")
# rotulos do trilho da esquerda com valor do lado ("LIG  02:34", "SO  LINUX")
_TRILHO = [(re.compile(r"^LIG\b"), "UP"), (re.compile(r"^SO\b"), "OS"),
           (re.compile(r"^REDE\b"), "NET"), (re.compile(r"^RAM\b"), "RAM")]


def _ui(chave_normal: str):
    if not _UI_N:
        for k, v in _UI.items():
            if not v:
                continue
            # o simbolo da frente ("◈ CAMERA") fica no texto original: tira da
            # chave (o _chave ja tira) E do ingles, senao sai duplicado
            if re.match(r"^[^\wÀ-ÿ\[]", k):
                v = re.sub(r"^[^\wÀ-ÿ\[]+\s*", "", v)
            _UI_N.setdefault(_chave(k), v)
    return _UI_N.get(chave_normal)


def _uma_linha(texto: str) -> str:
    """Uma linha: simbolo na frente e espacos preservados."""
    m = re.match(r"^(\s*[^\wÀ-ÿ\[\]]*\s*)(.*?)(\s*[✕✓●○◆◇◈◉■▸⊘]*\s*)$", texto, re.S)
    pre, meio, pos = (m.group(1), m.group(2), m.group(3)) if m else ("", texto, "")
    if not meio or not _TEM_LETRA.search(meio):
        return texto
    en = _ui(_chave(meio))
    if en:
        return pre + _mesma_caixa(meio, en) + pos
    for rx, novo in _TRILHO:
        if rx.match(meio):
            return pre + rx.sub(novo, meio, count=1) + pos
    if meio.startswith(("SIS:", "Voce:", "Você:", "Status:", "ARQ:", "ERRO:")):
        return pre + traduzir_linha(meio) + pos
    return texto


def traduzir_texto(texto):
    """Texto de botao/rotulo/desenho. Linhas soltas ("●\nOUVINDO") tambem."""
    if not isinstance(texto, str) or not texto.strip():
        return texto
    if texto in _CACHE:
        return _CACHE[texto]
    if "\n" not in texto:
        saida = _uma_linha(texto)
    else:
        # botao em varias linhas: simbolo numa, palavras nas outras
        linhas = texto.split("\n")
        com_letra = [i for i, l in enumerate(linhas) if _TEM_LETRA.search(l)]
        frase = " ".join(linhas[i].strip() for i in com_letra)
        en = _ui(_chave(frase)) if com_letra else None
        if en:
            en = _mesma_caixa(frase, en)
            # "[ESC]" e afins ficam na linha deles
            fixos = [i for i in com_letra if re.fullmatch(r"\s*\[[^\]]+\]\s*", linhas[i])]
            livres = [i for i in com_letra if i not in fixos]
            for i in fixos:
                en = en.replace(linhas[i].strip(), "").strip()
            palavras = en.split()
            n = max(1, len(livres))
            por_linha = max(1, -(-len(palavras) // n))
            pedacos = [" ".join(palavras[j:j + por_linha])
                       for j in range(0, len(palavras), por_linha)] or [""]
            while len(pedacos) < n:
                pedacos.append("")
            if len(pedacos) > n:
                pedacos = pedacos[:n - 1] + [" ".join(pedacos[n - 1:])]
            for i, pedaco in zip(livres, pedacos):
                linhas[i] = pedaco
            saida = "\n".join(linhas)
        else:
            saida = "\n".join(_uma_linha(l) for l in linhas)
    if len(_CACHE) < 5000:
        _CACHE[texto] = saida
    return saida


# ── ligar na janela ─────────────────────────────────────────────────────────
_INSTALADO = False


def instalar(app=None) -> None:
    """Liga a traducao na interface Qt inteira. Idempotente."""
    global _INSTALADO
    if _INSTALADO:
        return
    _INSTALADO = True
    from PyQt6 import QtGui, QtWidgets, QtCore  # noqa: PLC0415

    def _embrulha(cls, nome):
        original = getattr(cls, nome, None)
        if original is None:
            return

        def novo(self, *args, **kw):
            if args and isinstance(args[0], str):
                args = (traduzir_texto(args[0]),) + tuple(args[1:])
            return original(self, *args, **kw)
        try:
            setattr(cls, nome, novo)
        except Exception:
            pass

    for cls, nome in ((QtWidgets.QLabel, "setText"), (QtWidgets.QAbstractButton, "setText"),
                      (QtWidgets.QLineEdit, "setPlaceholderText"),
                      (QtWidgets.QTextEdit, "setPlaceholderText"),
                      (QtWidgets.QWidget, "setToolTip"), (QtWidgets.QWidget, "setWindowTitle"),
                      (QtWidgets.QGroupBox, "setTitle")):
        _embrulha(cls, nome)

    # O que e DESENHADO (status do meio, trilho da esquerda) passa pelo QPainter.
    _draw = QtGui.QPainter.drawText

    def draw_text(self, *args):
        args = tuple(traduzir_texto(a) if isinstance(a, str) else a for a in args)
        return _draw(self, *args)
    try:
        QtGui.QPainter.drawText = draw_text
    except Exception:
        pass

    # Widgets criados com o texto no construtor (QLabel("...")) nao passam
    # pelo setText: na primeira vez que aparecem, a arvore inteira e traduzida.
    class _AoMostrar(QtCore.QObject):
        def eventFilter(self, obj, ev):
            try:
                if ev.type() == QtCore.QEvent.Type.Show and isinstance(obj, QtWidgets.QWidget):
                    traduzir_arvore(obj)
            except Exception:
                pass
            return False

    app = app or QtWidgets.QApplication.instance()
    if app is not None:
        filtro = _AoMostrar(app)
        app.installEventFilter(filtro)
        globals()["_FILTRO"] = filtro


def traduzir_arvore(raiz) -> None:
    from PyQt6 import QtWidgets  # noqa: PLC0415
    widgets = [raiz] + raiz.findChildren(QtWidgets.QWidget)
    for w in widgets:
        try:
            if w.property("_jarvis_en"):
                continue
            w.setProperty("_jarvis_en", True)
            if isinstance(w, (QtWidgets.QLabel, QtWidgets.QAbstractButton)):
                t = w.text()
                if t:
                    w.setText(t)            # passa pelo setText embrulhado
            if isinstance(w, (QtWidgets.QLineEdit, QtWidgets.QTextEdit)):
                t = w.placeholderText()
                if t:
                    w.setPlaceholderText(t)
            t = w.toolTip()
            if t:
                w.setToolTip(t)
            if w.isWindow() and w.windowTitle():
                w.setWindowTitle(w.windowTitle())
            if isinstance(w, QtWidgets.QGroupBox) and w.title():
                w.setTitle(w.title())
        except Exception:
            continue
