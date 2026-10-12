from __future__ import annotations

"""Vault Constitution — Gera CLAUDE.md personalizado a partir do template."""

from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime
import re


@dataclass
class VaultBriefing:
    """Briefing do usuário para personalizar a constituição."""
    nome: str
    cidade: str
    o_que_vende: str
    icp_em_uma_frase: str
    tom_com_a_pessoa: str = "direto e telegráfico, sem enrolação"
    tom_da_marca: str = "profissional e acolhedor"
    foco_semana: str = ""
    prioridade_1: str = ""
    prioridade_2: str = ""
    prioridade_3: str = ""
    bloqueio_principal: str = ""

    def to_dict(self) -> dict:
        return {
            "NOME": self.nome,
            "CIDADE": self.cidade,
            "O_QUE_VENDE": self.o_que_vende,
            "ICP_EM_UMA_FRASE": self.icp_em_uma_frase,
            "TOM_COM_A_PESSOA": self.tom_com_a_pessoa,
            "TOM_DA_MARCA": self.tom_da_marca,
            "FOCO_SEMANA": self.foco_semana or datetime.now().strftime("%d/%m/%Y"),
            "PRIORIDADE_1": self.prioridade_1 or "[PREENCHER]",
            "PRIORIDADE_2": self.prioridade_2 or "[PREENCHER]",
            "PRIORIDADE_3": self.prioridade_3 or "[PREENCHER]",
            "BLOQUEIO_PRINCIPAL": self.bloqueio_principal or "[PREENCHER]",
        }


class VaultConstitutionGenerator:
    """Gera CLAUDE.md a partir do template e briefing."""

    def __init__(self, template_path: Optional[Path] = None, vault_path: Optional[Path] = None):
        # Default to vault assets folder
        if template_path is not None:
            self.template_path = template_path
        elif vault_path is not None:
            self.template_path = Path(vault_path) / "assets" / "CLAUDE-template.md"
        else:
            # Try default vault location
            default_vault = Path.home() / "Downloads" / "Jarvis-obsidian-teste"
            if (default_vault / "assets" / "CLAUDE-template.md").exists():
                self.template_path = default_vault / "assets" / "CLAUDE-template.md"
            else:
                # Fallback to source code assets if exists
                self.template_path = Path(__file__).resolve().parent.parent.parent.parent / "assets" / "CLAUDE-template.md"
        self._template: Optional[str] = None

    def _load_template(self) -> str:
        """Carrega template do arquivo."""
        if self._template is None:
            if not self.template_path.exists():
                raise FileNotFoundError(f"Template não encontrado: {self.template_path}")
            self._template = self.template_path.read_text(encoding="utf-8")
        return self._template

    def generate(self, briefing: VaultBriefing) -> str:
        """Gera CLAUDE.md preenchendo placeholders."""
        template = self._load_template()
        result = template

        # Substitui placeholders {{VARIAVEL}}
        for key, value in briefing.to_dict().items():
            placeholder = f"{{{{{key}}}}}"
            result = result.replace(placeholder, value)

        # Substitui placeholders restantes não preenchidos
        result = re.sub(r'\{\{[A-Z_]+\}\}', '[PREENCHER]', result)

        return result

    def install(self, vault_path: Path, briefing: VaultBriefing) -> Path:
        """Gera e instala CLAUDE.md na raiz do vault."""
        content = self.generate(briefing)
        target = Path(vault_path) / "CLAUDE.md"
        target.write_text(content, encoding="utf-8")
        print(f"[VaultConstitution] CLAUDE.md instalado em: {target}")
        return target

    def update_foco_semana(self, vault_path: Path, foco_semana: str,
                          prioridade_1: str = "", prioridade_2: str = "",
                          prioridade_3: str = "", bloqueio_principal: str = "") -> Path:
        """Atualiza apenas a seção 'Foco da semana' do CLAUDE.md existente."""
        target = Path(vault_path) / "CLAUDE.md"
        if not target.exists():
            raise FileNotFoundError(f"CLAUDE.md não encontrado: {target}")

        content = target.read_text(encoding="utf-8")

        # Substitui a seção 6
        new_section = f"""## 6. Foco da semana
> Atualizar toda segunda. Pesa todas as ações do sistema.

**Semana de:** {datetime.now().strftime("%d/%m/%Y")}
**Prioridade 1:** {prioridade_1 or "[PREENCHER]"}
**Prioridade 2:** {prioridade_2 or "[PREENCHER]"}
**Prioridade 3:** {prioridade_3 or "[PREENCHER]"}
**Bloqueio principal:** {bloqueio_principal or "[PREENCHER]"}"""

        # Regex para encontrar e substituir a seção 6
        pattern = r'## 6\. Foco da semana\n>.*?\n\n\*\*Semana de:\*\*.*?\n\*\*Bloqueio principal:\*\*.*'
        content = re.sub(pattern, new_section, content, flags=re.DOTALL)

        target.write_text(content, encoding="utf-8")
        print(f"[VaultConstitution] Foco da semana atualizado em: {target}")
        return target


def create_constitution_generator(template_path: Optional[Path] = None) -> VaultConstitutionGenerator:
    """Factory para criar gerador de constituição."""
    return VaultConstitutionGenerator(template_path)


def install_constitution(vault_path: Path, briefing: VaultBriefing,
                         template_path: Optional[Path] = None) -> Path:
    """Instala constituição personalizada no vault."""
    generator = create_constitution_generator(template_path)
    return generator.install(vault_path, briefing)


if __name__ == "__main__":
    # Teste rápido
    briefing = VaultBriefing(
        nome="Carlos Costa",
        cidade="Rio de Janeiro",
        o_que_vende="Direito previdenciário (9 tipos de benefícios e 13 tipos de serviços)",
        icp_em_uma_frase="Pessoas com dores do INSS que precisam de benefícios previdenciários, profissionais",
    )
    generator = create_constitution_generator()
    content = generator.generate(briefing)
    print(content[:500])