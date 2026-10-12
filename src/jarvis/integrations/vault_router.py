from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar

"""Vault Router — Roteamento centralizado baseado no CLAUDE.md (tabela gatilho→pasta)."""


class IntentType(Enum):
    """Tipos de intent reconhecidos pelo roteador."""
    CREATE_MEETING = "create_meeting"
    CREATE_IDEA = "create_idea"
    CREATE_LEAD = "create_lead"
    CREATE_TASK = "create_task"
    CREATE_NOTE = "create_note"
    CREATE_PROJECT = "create_project"
    RECORD_FINANCE = "record_finance"
    RECORD_PAYMENT = "record_payment"
    SEARCH = "search"
    UNKNOWN = "unknown"


@dataclass
class Intent:
    """Intent detectado do usuário."""
    type: IntentType
    confidence: float
    params: dict[str, Any] = field(default_factory=dict)
    raw_text: str = ""

    @property
    def is_action(self) -> bool:
        return self.type not in (IntentType.UNKNOWN, IntentType.SEARCH)


@dataclass
class VaultRouteResult:
    """Resultado do roteamento para vault."""
    success: bool
    note_path: str | None = None
    message: str = ""
    actions_taken: list[str] = field(default_factory=list)
    related_notes: list[str] = field(default_factory=list)


@dataclass
class MeetingParams:
    """Parâmetros para criar reunião."""
    who: str
    company: str | None = None
    when: str = "tomorrow"
    topic: str = "Reunião"
    time: str | None = None
    objective: str = ""
    agenda: list[str] = field(default_factory=list)


@dataclass
class LeadParams:
    """Parâmetros para criar lead/pessoa."""
    name: str
    company: str | None = None
    contact: str | None = None
    source: str | None = None
    status: str = "novo"


@dataclass
class IdeaParams:
    """Parâmetros para criar ideia de conteúdo."""
    topic: str
    format: str = "ideia"  # reel, carrossel, post, video, roteiro
    platform: str | None = None


class IntentClassifier:
    """Classificador de intents baseado em padrões regex + LLM fallback."""

    # Padrões regex para intents comuns (baseado no CLAUDE.md)
    PATTERNS: ClassVar[dict[IntentType, list[str]]] = {
        IntentType.CREATE_MEETING: [
            r"\b(reunião|reuniao|meeting|call|call de)\s+(amanhã|amanha|hoje|semana|próxima|proxima)\b",
            r"\b(vou ter|tenho|marcar|agendar)\s+(reunião|reuniao|meeting)\b",
            r"\b(reunião|reuniao)\s+com\s+\w+",
        ],
        IntentType.CREATE_IDEA: [
            r"\b(tive uma ideia|ideia de|pensei em)\s+(reel|carrossel|post|video|vídeo|conteúdo|conteudo)\b",
            r"\b(ideia\s+(para|de|sobre))\b",
            r"\binspiração\s+(para|de)\b",
        ],
        IntentType.CREATE_LEAD: [
            r"\b(fechei|consegui|novo)\s+(lead|cliente|oportunidade)\b",
            r"\b(novo\s+(lead|cliente|prospect))\b",
            r"\b(lead\s+quente|oportunidade)\b",
        ],
        IntentType.CREATE_TASK: [
            r"\b(tenho que|preciso|vou|devo)\s+(fazer|entregar|enviar|responder)\b",
            r"\b(tarefa|task|todo)\s+(nova|nova:)\b",
        ],
        IntentType.CREATE_NOTE: [
            r"\b(anotar|registrar|salvar)\s+(isso|aquele|aquilo)\b",
            r"\b(nota|note)\s+(sobre|para)\b",
        ],
        IntentType.CREATE_PROJECT: [
            r"\b(novo\s+(projeto|projeto:))\b",
            r"\b(começar|iniciar)\s+(projeto|projeto:)\b",
        ],
        IntentType.RECORD_FINANCE: [
            r"\b(gastei|paguei|comprei)\s+\d+",
            r"\b(despesa|gasto|custo)\s+(de|do|da)\b",
        ],
        IntentType.RECORD_PAYMENT: [
            r"\b(recebi|entrou)\s+\d+",
            r"\b(pagamento|recebimento)\s+(de|do|da)\b",
        ],
        IntentType.SEARCH: [
            r"\b(busca|buscar|procurar|encontrar)\s+",
            r"\b(o que|onde|quando|quem|como)\s+",
        ],
    }

    def __init__(self):
        self._compiled_patterns = {
            intent: [re.compile(p, re.IGNORECASE) for p in patterns]
            for intent, patterns in self.PATTERNS.items()
        }

    def classify(self, text: str) -> tuple[IntentType, float, dict]:
        """Classifica intent do texto."""
        text_lower = text.lower()
        best_intent = IntentType.UNKNOWN
        best_score = 0.0
        params = {}

        for intent, patterns in self._compiled_patterns.items():
            for pattern in patterns:
                match = pattern.search(text_lower)
                if match:
                    # Score baseado em especificidade do match
                    score = len(match.group(0)) / len(text_lower)
                    if score > best_score:
                        best_score = score
                        best_intent = intent
                        params = self._extract_params(intent, match, text)

        confidence = min(best_score * 2, 1.0)
        return best_intent, confidence, params

    def _extract_params(self, intent: IntentType, match: re.Match, text: str) -> dict:
        """Extrai parâmetros do match."""
        params = {}
        text_lower = text.lower()

        if intent == IntentType.CREATE_MEETING:
            # Extrai nome da pessoa
            name_match = re.search(r'com\s+(\w+(?:\s+\w+)?)', text, re.IGNORECASE)
            if name_match:
                params["who"] = name_match.group(1).strip()

            # Extrai empresa (para em preposições comuns: às, em, para, com, de, do, da, na, no)
            company_match = re.search(
                r'(?:da|do|na)\s+([\wÀ-ÿ]+(?:\s+[\wÀ-ÿ]+)?)(?=\s+(?:à[sz]?|em|para|com|de|do|da|na|no)\b|\s*$)',
                text, re.IGNORECASE
            )
            if company_match:
                params["company"] = company_match.group(1).strip()

            # Data
            if "amanhã" in text_lower or "amanha" in text_lower:
                params["when"] = "tomorrow"
            elif "hoje" in text_lower:
                params["when"] = "today"
            elif "semana" in text_lower:
                params["when"] = "this_week"

            # Horário
            time_match = re.search(r'(\d{1,2}:\d{2}|\d{1,2}h)', text)
            if time_match:
                params["time"] = time_match.group(1)

            # Tópico (o que sobra após "reunião")
            topic_match = re.search(r'(?:reunião|reuniao)\s+(?:com\s+\w+\s+)?(?:da\s+\w+\s+)?(.+)', text, re.IGNORECASE)
            if topic_match and len(topic_match.group(1).strip()) > 3:
                params["topic"] = topic_match.group(1).strip()

        elif intent == IntentType.CREATE_IDEA:
            topic_match = re.search(r'(?:ideia|ideia\s+de|ideia\s+para)\s+(.+)', text, re.IGNORECASE)
            if topic_match:
                params["topic"] = topic_match.group(1).strip()

            # Detecta formato
            if "reel" in text_lower:
                params["format"] = "reel"
            elif "carrossel" in text_lower or "carousel" in text_lower:
                params["format"] = "carrossel"
            elif "post" in text_lower:
                params["format"] = "post"
            elif "vídeo" in text_lower or "video" in text_lower:
                params["format"] = "video"
            elif "roteiro" in text_lower:
                params["format"] = "roteiro"

        elif intent == IntentType.CREATE_LEAD:
            name_match = re.search(r'(?:lead|cliente)\s+(\w+(?:\s+\w+)?)', text, re.IGNORECASE)
            if name_match:
                params["name"] = name_match.group(1).strip()

        return params

    def classify_intent(self, text: str) -> Intent:
        """Classifica e retorna objeto Intent."""
        intent_type, confidence, params = self.classify(text)
        return Intent(
            type=intent_type,
            confidence=confidence,
            params=params,
            raw_text=text
        )


class VaultRouter:
    """Roteador centralizado para vault (baseado na tabela do CLAUDE.md)."""

    def __init__(
        self,
        vault_path: Path,
        constitution_path: Path | None = None
    ):
        self.vault_path = Path(vault_path)
        self.constitution_path = constitution_path or (self.vault_path / "CLAUDE.md")
        self.classifier = IntentClassifier()
        self._routing_rules = self._load_routing_rules()

    def _load_routing_rules(self) -> dict:
        """Carrega regras de roteamento do CLAUDE.md ou usa defaults."""
        # Tenta extrair do CLAUDE.md se existir
        if self.constitution_path.exists():
            return self._parse_constitution_routing()

        # Defaults baseados no template
        return {
            "meeting_client": "clientes/{cliente}/reunioes/",
            "meeting_internal": "reunioes/{contexto}/",
            "lead": "pessoas/",
            "company": "empresas/",
            "deal": "deals/",
            "finance": "financas/",
            "idea": "conteudo/ideias/",
            "knowledge": "conhecimento/raw/",
            "project": "projetos/",
            "reference": "referencias/",
            "personal": "pessoal/",
            "unknown": "inbox/",
        }

    def _parse_constitution_routing(self) -> dict:
        """Extrai tabela de roteamento do CLAUDE.md."""
        content = self.constitution_path.read_text(encoding="utf-8")
        rules = {}

        # Extrai tabela de roteamento (seção 3)
        # Procura por linhas com padrão | gatilho | pasta |
        table_pattern = r'\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|'
        matches = re.findall(table_pattern, content)

        for trigger, folder in matches:
            trigger = trigger.strip()
            folder = folder.strip().strip('`')
            if trigger and folder and not trigger.startswith('Quando') and '|' not in trigger:
                # Normaliza chave
                key = re.sub(r'[^\w]+', '_', trigger.lower())[:30]
                rules[key] = folder

        # Fallback para defaults se não extraiu nada útil
        if not rules:
            return self._load_routing_rules()

        return rules

    def route(self, intent: Intent, vault_manager=None) -> dict:
        """Roteia intent para ação no vault."""
        if intent.type == IntentType.CREATE_MEETING:
            return self._route_meeting(intent)
        if intent.type == IntentType.CREATE_IDEA:
            return self._route_idea(intent)
        if intent.type == IntentType.CREATE_LEAD:
            return self._route_lead(intent)
        if intent.type == IntentType.CREATE_TASK:
            return self._route_task(intent)
        if intent.type == IntentType.RECORD_FINANCE:
            return self._route_finance(intent)
        if intent.type == IntentType.RECORD_PAYMENT:
            return self._route_payment(intent)
        if intent.type == IntentType.CREATE_NOTE:
            return self._route_note(intent)
        if intent.type == IntentType.CREATE_PROJECT:
            return self._route_project(intent)
        if intent.type == IntentType.SEARCH:
            return {"action": "search", "query": intent.params.get("query", intent.raw_text)}

        return {"action": "inbox", "message": "Não sei rotear, depositando no inbox/"}

    def _resolve_client_folder(self, company: str) -> tuple[str, bool]:
        """Verifica se empresa é cliente ativo (tem pasta em clientes/)."""
        client_path = self.vault_path / "clientes" / company.lower().replace(" ", "-")
        is_client = client_path.exists() and client_path.is_dir()
        return company.lower().replace(" ", "-"), is_client

    def _route_meeting(self, intent: Intent) -> dict:
        params = intent.params
        who = params.get("who", "alguém")
        company = params.get("company")
        when = params.get("when", "tomorrow")
        topic = params.get("topic", "Reunião")
        time = params.get("time", "")

        # Determina pasta baseado em empresa ser cliente ou não
        if company:
            client_slug, is_client = self._resolve_client_folder(company)
            if is_client:
                folder = f"clientes/{client_slug}/reunioes/"
            else:
                folder = f"reunioes/{client_slug}/"
        else:
            folder = f"reunioes/{who.lower().replace(' ', '-')}/"

        date_str = datetime.now().strftime("%Y-%m-%d")
        filename = f"{date_str}-{who.lower().replace(' ', '-')}.md"

        return {
            "action": "create_meeting",
            "folder": folder,
            "filename": filename,
            "params": {
                "who": who,
                "company": company,
                "when": when,
                "topic": topic,
                "time": time,
            }
        }

    def _route_idea(self, intent: Intent) -> dict:
        topic = intent.params.get("topic", "ideia")
        format_type = intent.params.get("format", "ideia")

        return {
            "action": "create_idea",
            "folder": "conteudo/ideias/",
            "filename": f"{datetime.now().strftime('%Y-%m-%d')}-{format_type}-{topic[:30]}.md",
            "params": {"topic": topic, "format": format_type}
        }

    def _route_lead(self, intent: Intent) -> dict:
        name = intent.params.get("name", "novo-lead")

        return {
            "action": "create_lead",
            "folder": "pessoas/",
            "filename": f"{datetime.now().strftime('%Y-%m-%d')}-{name.lower().replace(' ', '-')}.md",
            "params": {"name": name}
        }

    def _route_task(self, intent: Intent) -> dict:
        return {
            "action": "create_task",
            "folder": "inbox/",
            "filename": f"{datetime.now().strftime('%Y-%m-%d')}-task.md",
            "params": {"task": intent.raw_text}
        }

    def _route_finance(self, intent: Intent) -> dict:
        return {
            "action": "record_expense",
            "folder": "financas/",
            "filename": f"{datetime.now().strftime('%Y-%m-%d')}-despesa.md",
            "params": {"text": intent.raw_text}
        }

    def _route_payment(self, intent: Intent) -> dict:
        return {
            "action": "record_payment",
            "folder": "financas/",
            "filename": f"{datetime.now().strftime('%Y-%m-%d')}-recebimento.md",
            "params": {"text": intent.raw_text}
        }

    def _route_note(self, intent: Intent) -> dict:
        return {
            "action": "create_note",
            "folder": "inbox/",
            "filename": f"{datetime.now().strftime('%Y-%m-%d')}-note.md",
            "params": {"text": intent.raw_text}
        }

    def _route_project(self, intent: Intent) -> dict:
        return {
            "action": "create_project",
            "folder": "projetos/",
            "filename": f"{datetime.now().strftime('%Y-%m-%d')}-projeto.md",
            "params": {"text": intent.raw_text}
        }

    def build_note_content(self, route_result: dict) -> tuple[str, dict]:
        """Constrói conteúdo e frontmatter da nota baseado na ação."""
        action = route_result.get("action")
        params = route_result.get("params", {})

        templates = {
            "create_meeting": self._template_meeting,
            "create_idea": self._template_idea,
            "create_lead": self._template_lead,
            "create_task": self._template_task,
            "record_expense": self._template_finance,
            "record_payment": self._template_payment,
            "create_note": self._template_note,
            "create_project": self._template_project,
        }

        template_fn = templates.get(action, self._template_generic)
        content = template_fn(params)
        frontmatter = {
            "type": action,
            "created": datetime.now().isoformat(),
            "source": "jarvis_voice",
            **params
        }

        return content, frontmatter

    def _template_meeting(self, p: dict) -> str:
        return f"""# Reunião: {p.get('topic', 'Reunião')}

**Data:** {datetime.now().strftime('%Y-%m-%d')}
**Com:** {p.get('who', 'A definir')}
**Empresa:** {p.get('company', 'Não especificada')}
**Quando:** {p.get('when', 'Amanhã')}
**Horário:** {p.get('time', 'A definir')}

---

## Objetivo

[Definir objetivo da reunião]

---

## Pauta

1. 
2. 
3. 

---

## Notas

---

## Próximos Passos

- [ ] 
- [ ] 

---
*Criado via JARVIS em {datetime.now().strftime('%Y-%m-%d %H:%M')}*
"""

    def _template_idea(self, p: dict) -> str:
        return f"""# Ideia: {p.get('topic', 'Nova Ideia')}

**Data:** {datetime.now().strftime('%Y-%m-%d')}
**Formato:** {p.get('format', 'ideia')}
**Origem:** Captura de voz

---

## Descrição

{p.get('topic', '')}

---

## Próximos Passos

- [ ] Desenvolver conceito
- [ ] Validar
- [ ] Produzir

---
*Capturado via JARVIS em {datetime.now().strftime('%Y-%m-%d %H:%M')}*
"""

    def _template_lead(self, p: dict) -> str:
        return f"""# Lead: {p.get('name', 'Novo Lead')}

**Data:** {datetime.now().strftime('%Y-%m-%d')}
**Status:** Novo

---

## Informações

- **Nome:** {p.get('name', 'Novo Lead')}
- **Empresa:** {p.get('company', 'Não informada')}
- **Contato:** {p.get('contact', 'Não informado')}

---

## Histórico

- {datetime.now().strftime('%Y-%m-%d %H:%M')} - Lead criado via JARVIS

---

## Próximos Passos

- [ ] Qualificar
- [ ] Agendar call
- [ ] Enviar proposta

---
*Criado via JARVIS em {datetime.now().strftime('%Y-%m-%d %H:%M')}*
"""

    def _template_task(self, p: dict) -> str:
        return f"""# Tarefa

**Data:** {datetime.now().strftime('%Y-%m-%d')}

---

## Descrição

{p.get('task', '')}

---

## Status

- [ ] Pendente

---
*Criado via JARVIS em {datetime.now().strftime('%Y-%m-%d %H:%M')}*
"""

    def _template_finance(self, p: dict) -> str:
        return f"""# Despesa

**Data:** {datetime.now().strftime('%Y-%m-%d')}

---

## Detalhes

{p.get('text', '')}

---
*Registrado via JARVIS em {datetime.now().strftime('%Y-%m-%d %H:%M')}*
"""

    def _template_payment(self, p: dict) -> str:
        return f"""# Recebimento

**Data:** {datetime.now().strftime('%Y-%m-%d')}

---

## Detalhes

{p.get('text', '')}

---
*Registrado via JARVIS em {datetime.now().strftime('%Y-%m-%d %H:%M')}*
"""

    def _template_note(self, p: dict) -> str:
        return f"""# Nota

**Data:** {datetime.now().strftime('%Y-%m-%d')}

---

{p.get('text', '')}

---
*Criado via JARVIS em {datetime.now().strftime('%Y-%m-%d %H:%M')}*
"""

    def _template_project(self, p: dict) -> str:
        return f"""# Projeto

**Data:** {datetime.now().strftime('%Y-%m-%d')}

---

## Descrição

{p.get('text', '')}

---

## Próximos Passos

- [ ] Definir escopo
- [ ] Planejar
- [ ] Executar

---
*Criado via JARVIS em {datetime.now().strftime('%Y-%m-%d %H:%M')}*
"""

    def _template_generic(self, p: dict) -> str:
        return f"""# Nota Genérica

**Data:** {datetime.now().strftime('%Y-%m-%d')}

---

{p.get('text', str(p))}

---
*Criado via JARVIS em {datetime.now().strftime('%Y-%m-%d %H:%M')}*
"""

    async def execute_route(self, route_result: dict, vault_manager) -> dict:
        """Executa a ação roteada no vault via vault_manager."""
        action = route_result.get("action")

        if action in ["create_meeting", "create_note", "create_lead", "create_idea",
                      "create_task", "create_project", "record_expense", "record_payment"]:
            folder = route_result.get("folder", "inbox/")
            filename = route_result.get("filename", f"{datetime.now().strftime('%Y-%m-%d')}-note.md")
            full_path = f"{folder}{filename}"

            content, frontmatter = self.build_note_content(route_result)

            success = await vault_manager.write_note(
                path=full_path,
                content=content,
                frontmatter=frontmatter
            )

            if success:
                return {"success": True, "path": full_path, "message": f"Criado: {full_path}"}
            return {"success": False, "message": f"Falha ao criar: {full_path}"}

        return {"success": False, "message": f"Ação {action} não implementada"}


def create_vault_router(vault_path: Path, constitution_path: Path | None = None) -> VaultRouter:
    """Factory para criar VaultRouter."""
    return VaultRouter(vault_path, constitution_path)
