from __future__ import annotations

import re
import json
import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Any, Callable, Set
from pathlib import Path
from datetime import datetime

from ..paths import get_jarvis_paths
from ..core.telemetry import trace, get_telemetry


class RiskLevel(Enum):
    SAFE = "safe"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ActionCategory(Enum):
    FILE_READ = "file_read"
    FILE_WRITE = "file_write"
    FILE_DELETE = "file_delete"
    SYSTEM_COMMAND = "system_command"
    NETWORK_REQUEST = "network_request"
    PROCESS_SPAWN = "process_spawn"
    REGISTRY_ACCESS = "registry_access"
    USER_DATA_ACCESS = "user_data_access"
    CREDENTIAL_ACCESS = "credential_access"
    SYSTEM_CONFIG_CHANGE = "system_config_change"


@dataclass
class GuardrailRule:
    id: str
    name: str
    description: str
    category: ActionCategory
    risk_level: RiskLevel
    pattern: Optional[str] = None  # Regex pattern for matching
    condition: Optional[Callable[[Dict], bool]] = None  # Custom condition function
    action: str = "block"  # block, warn, allow, require_approval
    message: str = ""
    enabled: bool = True
    metadata: Dict = field(default_factory=dict)


@dataclass
class GuardrailDecision:
    allowed: bool
    risk_level: RiskLevel
    rule_id: Optional[str]
    reason: str
    requires_approval: bool = False
    approval_id: Optional[str] = None
    alternative_suggestion: Optional[str] = None


@dataclass
class ApprovalRequest:
    id: str
    action_name: str
    action_args: Dict
    risk_level: RiskLevel
    reason: str
    requested_at: datetime
    expires_at: Optional[datetime] = None
    status: str = "pending"  # pending, approved, denied, expired
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None


class GuardrailsEngine:
    def __init__(self, config_path: Optional[Path] = None):
        self.rules: Dict[str, GuardrailRule] = {}
        self.approvals: Dict[str, ApprovalRequest] = {}
        self.audit_log: List[Dict] = []
        self._approval_callbacks: List[Callable[[ApprovalRequest], None]] = []
        self._load_default_rules()
        if config_path:
            self.load_config(config_path)

    def _load_default_rules(self):
        """Carrega regras de segurança padrão."""

        # File system rules
        self.add_rule(GuardrailRule(
            id="file_delete_system",
            name="Delete System Files",
            description="Bloqueia exclusão de arquivos de sistema críticos",
            category=ActionCategory.FILE_DELETE,
            risk_level=RiskLevel.CRITICAL,
            pattern=r"(?i)(/windows|/boot|/etc|/sys|/proc|C:\\Windows|C:\\Program Files)",
            action="block",
            message="Exclusão de arquivos de sistema não permitida",
        ))

        self.add_rule(GuardrailRule(
            id="file_write_sensitive",
            name="Write to Sensitive Locations",
            description="Requer aprovação para escrita em locais sensíveis",
            category=ActionCategory.FILE_WRITE,
            risk_level=RiskLevel.HIGH,
            pattern=r"(?i)(/etc|/root|/home/.*/\.ssh|C:\\Users\\.*\\AppData|C:\\Windows)",
            action="require_approval",
            message="Escrita em local sensível requer aprovação",
        ))

        self.add_rule(GuardrailRule(
            id="file_read_credentials",
            name="Read Credential Files",
            description="Bloqueia leitura de arquivos de credenciais",
            category=ActionCategory.USER_DATA_ACCESS,
            risk_level=RiskLevel.CRITICAL,
            pattern=r"(?i)(\.env|id_rsa|\.pem|\.key|password|secret|token)",
            action="block",
            message="Leitura de arquivos de credenciais não permitida",
        ))

        # System command rules
        self.add_rule(GuardrailRule(
            id="system_command_dangerous",
            name="Dangerous System Commands",
            description="Bloqueia comandos de sistema perigosos",
            category=ActionCategory.SYSTEM_COMMAND,
            risk_level=RiskLevel.CRITICAL,
            pattern=r"(?i)(rm -rf|format|fdisk|mkfs|dd if=|shutdown|reboot|kill -9|taskkill /f)",
            action="block",
            message="Comando de sistema perigoso bloqueado",
        ))

        self.add_rule(GuardrailRule(
            id="system_command_sudo",
            name="Sudo/Admin Commands",
            description="Requer aprovação para comandos com privilégios elevados",
            category=ActionCategory.SYSTEM_COMMAND,
            risk_level=RiskLevel.HIGH,
            pattern=r"(?i)(sudo|runas|psexec|runas /user:administrator)",
            action="require_approval",
            message="Comando com privilégios elevados requer aprovação",
        ))

        # Network rules
        self.add_rule(GuardrailRule(
            id="network_external",
            name="External Network Requests",
            description="Monitora requisições de rede externas",
            category=ActionCategory.NETWORK_REQUEST,
            risk_level=RiskLevel.MEDIUM,
            pattern=r"(?i)(http://|https://|ftp://|ssh://)",
            action="warn",
            message="Requisição de rede externa detectada",
        ))

        self.add_rule(GuardrailRule(
            id="network_internal_private",
            name="Private Network Access",
            description="Alerta para acesso a redes privadas",
            category=ActionCategory.NETWORK_REQUEST,
            risk_level=RiskLevel.LOW,
            pattern=r"(?i)(192\.168\.|10\.|172\.(1[6-9]|2[0-9]|3[0-1])\.)",
            action="warn",
            message="Acesso a rede privada detectado",
        ))

        # Process rules
        self.add_rule(GuardrailRule(
            id="process_spawn_shell",
            name="Shell Spawning",
            description="Monitora criação de shells",
            category=ActionCategory.PROCESS_SPAWN,
            risk_level=RiskLevel.MEDIUM,
            pattern=r"(?i)(cmd\.exe|powershell|bash|sh|zsh|fish)",
            action="warn",
            message="Shell spawn detectado",
        ))

        # Credential access
        self.add_rule(GuardrailRule(
            id="credential_env_access",
            name="Environment Credential Access",
            description="Monitora acesso a variáveis de ambiente sensíveis",
            category=ActionCategory.CREDENTIAL_ACCESS,
            risk_level=RiskLevel.HIGH,
            pattern=r"(?i)(api_key|secret|password|token|private_key)",
            action="warn",
            message="Acesso a credencial em variável de ambiente",
        ))

        # Registry (Windows)
        self.add_rule(GuardrailRule(
            id="registry_modify",
            name="Registry Modification",
            description="Requer aprovação para modificação do registro",
            category=ActionCategory.REGISTRY_ACCESS,
            risk_level=RiskLevel.HIGH,
            pattern=r"(?i)(reg add|reg delete|regedit|Set-ItemProperty.*HKLM|HKCU)",
            action="require_approval",
            message="Modificação do registro requer aprovação",
        ))

    def add_rule(self, rule: GuardrailRule):
        self.rules[rule.id] = rule

    def remove_rule(self, rule_id: str) -> bool:
        if rule_id in self.rules:
            del self.rules[rule_id]
            return True
        return False

    def enable_rule(self, rule_id: str) -> bool:
        if rule_id in self.rules:
            self.rules[rule_id].enabled = True
            return True
        return False

    def disable_rule(self, rule_id: str) -> bool:
        if rule_id in self.rules:
            self.rules[rule_id].enabled = False
            return True
        return False

    @trace("guardrails.check_action")
    def check_action(self, action_name: str, action_args: Dict, category: ActionCategory = None) -> GuardrailDecision:
        """Verifica se uma ação é permitida segundo as regras."""
        telemetry = get_telemetry()
        matched_rules = []

        # Verifica cada regra
        for rule in self.rules.values():
            if not rule.enabled:
                continue

            # Filtra por categoria se especificada
            if category and rule.category != category:
                continue

            # Verifica padrão regex
            match = False
            check_text = json.dumps(action_args, ensure_ascii=False)
            if rule.pattern:
                if re.search(rule.pattern, check_text, re.IGNORECASE):
                    match = True

            # Verifica condição customizada
            if rule.condition:
                try:
                    if rule.condition(action_args):
                        match = True
                except Exception:
                    pass

            if match:
                matched_rules.append(rule)

        if not matched_rules:
            return GuardrailDecision(
                allowed=True,
                risk_level=RiskLevel.SAFE,
                rule_id=None,
                reason="Nenhuma regra correspondente",
            )

        # Pega a regra mais restritiva
        risk_order = {
            RiskLevel.CRITICAL: 5,
            RiskLevel.HIGH: 4,
            RiskLevel.MEDIUM: 3,
            RiskLevel.LOW: 2,
            RiskLevel.SAFE: 1,
        }
        highest_rule = max(matched_rules, key=lambda r: risk_order.get(r.risk_level, 0))
        highest_risk = highest_rule.risk_level

        # Determina ação final
        actions = [r.action for r in matched_rules]
        if "block" in actions:
            return GuardrailDecision(
                allowed=False,
                risk_level=highest_risk,
                rule_id=highest_rule.id,
                reason=highest_rule.message or "Ação bloqueada por política de segurança",
                requires_approval=False,
            )
        elif "require_approval" in actions:
            return GuardrailDecision(
                allowed=False,
                risk_level=highest_risk,
                rule_id=highest_rule.id,
                reason=highest_rule.message or "Ação requer aprovação",
                requires_approval=True,
                approval_id=self._create_approval_request(
                    action_name="action",
                    action_args=action_args,
                    risk_level=highest_risk,
                    reason=highest_rule.message,
                ),
            )
        elif "warn" in actions:
            return GuardrailDecision(
                allowed=True,
                risk_level=highest_risk,
                rule_id=highest_rule.id,
                reason=highest_rule.message or "Ação permitida com aviso",
            )

        return GuardrailDecision(
            allowed=True,
            risk_level=RiskLevel.SAFE,
            rule_id=None,
            reason="Permitido",
        )

    def _create_approval_request(self, action_name: str, action_args: Dict, risk_level: RiskLevel, reason: str) -> str:
        """Cria solicitação de aprovação."""
        import uuid
        approval_id = str(uuid.uuid4())[:8]
        request = ApprovalRequest(
            id=approval_id,
            action_name=action_name,
            action_args=action_args,
            risk_level=risk_level,
            reason=reason,
            requested_at=datetime.now(),
        )
        self.approvals[approval_id] = request

        # Notifica callbacks
        for callback in self._approval_callbacks:
            try:
                callback(self.approvals[approval_id])
            except Exception:
                pass

        return approval_id

    def approve(self, approval_id: str, approver: str) -> bool:
        if approval_id in self.approvals:
            approval = self.approvals[approval_id]
            approval.status = "approved"
            approval.approved_by = approver
            approval.approved_at = datetime.now()
            return True
        return False

    def deny(self, approval_id: str, denier: str) -> bool:
        if approval_id in self.approvals:
            approval = self.approvals[approval_id]
            approval.status = "denied"
            approval.approved_by = denier
            approval.approved_at = datetime.now()
            return True
        return False

    def get_pending_approvals(self) -> List[ApprovalRequest]:
        return [a for a in self.approvals.values() if a.status == "pending"]

    def add_approval_callback(self, callback: Callable[[ApprovalRequest], None]):
        self._approval_callbacks.append(callback)

    def log_audit(self, action: str, decision: GuardrailDecision, user: str = "system"):
        """Registra decisão no log de auditoria."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "user": user,
            "action": action,
            "decision": {
                "allowed": decision.allowed,
                "risk_level": decision.risk_level.value,
                "rule_id": decision.rule_id,
                "reason": decision.reason,
                "requires_approval": decision.requires_approval,
            },
        }
        self.audit_log.append(entry)

        # Manter apenas últimos 10000
        if len(self.audit_log) > 10000:
            self.audit_log = self.audit_log[-10000:]

    def get_audit_log(self, limit: int = 100) -> List[Dict]:
        return self.audit_log[-limit:]

    def save_config(self, path: Path):
        """Salva regras customizadas."""
        config = {
            "rules": [
                {
                    "id": r.id,
                    "name": r.name,
                    "description": r.description,
                    "category": r.category.value,
                    "risk_level": r.risk_level.value,
                    "pattern": r.pattern,
                    "action": r.action,
                    "message": r.message,
                    "enabled": r.enabled,
                    "metadata": r.metadata,
                }
                for r in self.rules.values() if r.id not in self._get_default_rule_ids()
            ]
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)

    def load_config(self, path: Path):
        if not path.exists():
            return
        with open(path, "r", encoding="utf-8") as f:
            config = json.load(f)

        for rule_data in config.get("rules", []):
            rule = GuardrailRule(
                id=rule_data["id"],
                name=rule_data["name"],
                description=rule_data["description"],
                category=ActionCategory(rule_data["category"]),
                risk_level=RiskLevel(rule_data["risk_level"]),
                pattern=rule_data.get("pattern"),
                action=rule_data["action"],
                message=rule_data["message"],
                enabled=rule_data.get("enabled", True),
                metadata=rule_data.get("metadata", {}),
            )
            self.add_rule(rule)

    def _get_default_rule_ids(self) -> Set[str]:
        return {
            "file_delete_system", "file_write_sensitive", "file_read_credentials",
            "system_command_dangerous", "system_command_sudo",
            "network_external", "network_internal_private",
            "process_spawn_shell", "credential_env_access", "registry_modify",
        }

    def get_rules_summary(self) -> List[Dict]:
        return [
            {
                "id": r.id,
                "name": r.name,
                "category": r.category.value,
                "risk_level": r.risk_level.value,
                "action": r.action,
                "enabled": r.enabled,
            }
            for r in self.rules.values()
        ]


# Global guardrails instance
_guardrails: Optional[GuardrailsEngine] = None


def get_guardrails() -> GuardrailsEngine:
    global _guardrails
    if _guardrails is None:
        _guardrails = GuardrailsEngine()
    return _guardrails


def init_guardrails(config_path: Path = None) -> GuardrailsEngine:
    global _guardrails
    paths = get_jarvis_paths()
    config_path = config_path or paths.config / "guardrails.json"
    _guardrails = GuardrailsEngine(config_path)
    return _guardrails