from __future__ import annotations

"""Vault Setup — Replica setup-vault.sh em Python para criar estrutura do Segundo Cérebro."""

from pathlib import Path
from dataclasses import dataclass
from typing import List, Tuple


@dataclass
class VaultFolder:
    """Representa uma pasta do vault com sua descrição."""
    name: str
    description: str
    subfolders: List[str] = None

    def __post_init__(self):
        if self.subfolders is None:
            self.subfolders = []


# Estrutura baseada no setup-vault.sh original
VAULT_STRUCTURE: List[VaultFolder] = [
    VaultFolder("inbox", "Captura rápida ainda não roteada. Tudo que chega e você ainda não sabe onde guardar cai aqui."),
    VaultFolder("diario", "Notas diárias (YYYY-MM-DD) e registro cronológico do que você fez no dia."),
    VaultFolder("pessoas", "Uma nota por pessoa: leads, parceiros, mentores, contatos. Quem é fulano, histórico de conversa."),
    VaultFolder("empresas", "Ficha curta de empresa externa que ainda não é cliente. Hub que liga pessoas e negócios."),
    VaultFolder("clientes", "Uma pasta por cliente ativo (self-contained): overview, reunioes, propostas, entregas."),
    VaultFolder("reunioes", "Reuniões sem dono de cliente (internas, parcerias). Subpastas por contexto."),
    VaultFolder("deals", "Negócios em andamento: leads quentes, oportunidades, fechamentos. Seu funil."),
    VaultFolder("financas", "Dinheiro: entradas, saídas, status de pagamento, contratos e valores."),
    VaultFolder("projetos", "Entregas sob demanda: landing page, sistema, documento, proposta. Uma pasta por projeto."),
    VaultFolder("conteudo", "Tudo de conteúdo: roteiros, carrosseis, ideias, posts. Subpasta ideias/ pro que ainda é semente.", ["ideias"]),
    VaultFolder("conhecimento", "Sua wiki. raw/ = material bruto por processar. wiki/ = notas limpas e interligadas.", ["raw", "wiki"]),
    VaultFolder("pessoal", "Vida pessoal: metas, rotina, saúde, finanças pessoais, planejamento."),
    VaultFolder("referencias", "Swipe file, voz, estilo, leituras, prints que te inspiram."),
    VaultFolder("arquivo", "Histórico e encerrado. Nunca é ativo. Arquive aqui com timestamp em vez de deletar."),
]


@dataclass
class ValidationResult:
    """Resultado da validação da estrutura do vault."""
    valid: bool
    missing_folders: List[str]
    missing_readmes: List[str]
    missing_subfolders: List[str]
    extra_items: List[str]


class VaultSetup:
    """Gerencia criação e validação da estrutura do vault."""

    def __init__(self, vault_path: Path):
        self.vault_path = Path(vault_path).resolve()

    def create_structure(self) -> None:
        """Cria toda a estrutura de pastas + READMEs + subpastas."""
        self.vault_path.mkdir(parents=True, exist_ok=True)

        for folder in VAULT_STRUCTURE:
            folder_path = self.vault_path / folder.name
            folder_path.mkdir(parents=True, exist_ok=True)

            # Cria README.md se não existir
            readme_path = folder_path / "README.md"
            if not readme_path.exists():
                readme_path.write_text(
                    f"# {folder.name}\n\n{folder.description}\n",
                    encoding="utf-8"
                )

            # Cria subpastas
            for subfolder in folder.subfolders:
                (folder_path / subfolder).mkdir(parents=True, exist_ok=True)
                # README para subpasta
                sub_readme = folder_path / subfolder / "README.md"
                if not sub_readme.exists():
                    sub_readme.write_text(
                        f"# {folder.name}/{subfolder}\n\n{subfolder.capitalize()} de {folder.name}.\n",
                        encoding="utf-8"
                    )

        print(f"[VaultSetup] Estrutura criada em: {self.vault_path}")

    def validate_structure(self) -> ValidationResult:
        """Valida se a estrutura está completa."""
        missing_folders = []
        missing_readmes = []
        missing_subfolders = []
        extra_items = []

        for folder in VAULT_STRUCTURE:
            folder_path = self.vault_path / folder.name

            if not folder_path.exists():
                missing_folders.append(folder.name)
                continue

            readme_path = folder_path / "README.md"
            if not readme_path.exists():
                missing_readmes.append(folder.name)

            for subfolder in folder.subfolders:
                sub_path = folder_path / subfolder
                if not sub_path.exists():
                    missing_subfolders.append(f"{folder.name}/{subfolder}")

        # Verifica itens extras na raiz (opcional)
        expected_names = {f.name for f in VAULT_STRUCTURE}
        for item in self.vault_path.iterdir():
            if item.is_dir() and item.name not in expected_names and not item.name.startswith('.'):
                extra_items.append(item.name)

        valid = not (missing_folders or missing_readmes or missing_subfolders)

        return ValidationResult(
            valid=valid,
            missing_folders=missing_folders,
            missing_readmes=missing_readmes,
            missing_subfolders=missing_subfolders,
            extra_items=extra_items
        )

    def print_validation(self, result: ValidationResult) -> None:
        """Imprime resultado da validação de forma legível."""
        if result.valid:
            print("[VaultSetup] ✅ Estrutura válida e completa")
        else:
            print("[VaultSetup] ❌ Problemas encontrados:")
            for f in result.missing_folders:
                print(f"  - Pasta faltando: {f}/")
            for f in result.missing_readmes:
                print(f"  - README faltando: {f}/README.md")
            for f in result.missing_subfolders:
                print(f"  - Subpasta faltando: {f}/")
            for f in result.extra_items:
                print(f"  - Item extra na raiz: {f}/")


def create_vault_structure(vault_path: Path) -> VaultSetup:
    """Factory para criar e inicializar estrutura do vault."""
    setup = VaultSetup(vault_path)
    setup.create_structure()
    return setup


def validate_vault_structure(vault_path: Path) -> ValidationResult:
    """Valida estrutura existente."""
    setup = VaultSetup(vault_path)
    result = setup.validate_structure()
    setup.print_validation(result)
    return result


if __name__ == "__main__":
    import sys
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    setup = create_vault_structure(path)
    result = setup.validate_structure()
    setup.print_validation(result)