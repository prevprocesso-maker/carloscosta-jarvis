from __future__ import annotations

"""OpenSquad Skills Bridge - Integra skills do OpenSquad no JARVIS."""

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml


@dataclass
class OpenSquadSkill:
    """Representa uma skill do OpenSquad."""
    name: str
    description: str
    version: str = "1.0.0"
    author: str = ""
    tags: List[str] = field(default_factory=list)
    parameters: Dict[str, Any] = field(default_factory=dict)
    scripts: Dict[str, str] = field(default_factory=dict)  # script_name -> path
    raw_content: str = ""
    timeout: int = 30  # Default timeout em segundos

    def to_action_schema(self) -> Dict[str, Any]:
        """Converte para schema de action do JARVIS (OpenAI function calling format)."""
        properties = {}
        required = []

        for param_name, param_info in self.parameters.items():
            if isinstance(param_info, dict):
                properties[param_name] = {
                    "type": param_info.get("type", "string"),
                    "description": param_info.get("description", ""),
                }
                if "enum" in param_info:
                    properties[param_name]["enum"] = param_info["enum"]
                if "default" in param_info:
                    properties[param_name]["default"] = param_info["default"]
                if param_info.get("required", False):
                    required.append(param_name)
            else:
                properties[param_name] = {"type": "string", "description": str(param_info)}

        return {
            "type": "function",
            "function": {
                "name": f"opensquad_{self.name.replace('-', '_')}",
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }


class OpenSquadSkillsRegistry:
    """Registry de skills do OpenSquad integradas ao JARVIS."""
    
    _instance: Optional["OpenSquadSkillsRegistry"] = None
    
    def __new__(cls, skills_dir: Path = None):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self, skills_dir: Path = None):
        if self._initialized:
            return
        self.skills_dir = skills_dir or Path("skills")
        self._skills: Dict[str, OpenSquadSkill] = {}
        self._loaded = False
        self._initialized = True

    def load_skills(self) -> Dict[str, OpenSquadSkill]:
        """Carrega todas as skills do diretório skills/."""
        if self._loaded:
            return self._skills

        if not self.skills_dir.exists():
            print(f"[OpenSquad] Diretório de skills não encontrado: {self.skills_dir}")
            return {}

        for skill_dir in self.skills_dir.iterdir():
            if not skill_dir.is_dir():
                continue

            skill_file = skill_dir / "SKILL.md"
            if not skill_file.exists():
                continue

            try:
                skill = self._parse_skill_file(skill_dir, skill_file)
                self._skills[skill.name] = skill
                print(f"[OpenSquad] Skill carregada: {skill.name}")
            except Exception as e:
                print(f"[OpenSquad] Erro ao carregar skill {skill_dir.name}: {e}")

        self._loaded = True
        return self._skills

    def _parse_skill_file(self, skill_dir: Path, skill_file: Path) -> OpenSquadSkill:
        """Parse do arquivo SKILL.md (formato frontmatter + markdown)."""
        content = skill_file.read_text(encoding="utf-8")

        # Extrai frontmatter YAML
        frontmatter = {}
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                try:
                    frontmatter = yaml.safe_load(parts[1]) or {}
                except yaml.YAMLError:
                    pass

        # Extrai scripts disponíveis
        scripts = {}
        scripts_dir = skill_dir / "scripts"
        if scripts_dir.exists():
            for script_file in scripts_dir.iterdir():
                if script_file.is_file():
                    scripts[script_file.stem] = str(script_file)

        # Extrai parâmetros do frontmatter ou do conteúdo
        parameters = frontmatter.get("parameters", {})
        if not parameters:
            parameters = self._extract_parameters_from_content(content)

        return OpenSquadSkill(
            name=frontmatter.get("name", skill_dir.name),
            description=frontmatter.get("description", ""),
            version=frontmatter.get("version", "1.0.0"),
            author=frontmatter.get("author", ""),
            tags=frontmatter.get("tags", []),
            parameters=parameters,
            scripts=scripts,
            raw_content=content,
        )

    def _extract_parameters_from_content(self, content: str) -> Dict[str, Any]:
        """Tenta extrair parâmetros do conteúdo markdown se não houver frontmatter."""
        parameters = {}

        # Procura por tabelas de parâmetros no markdown
        param_table_pattern = r'\|\s*Parameter\s*\|\s*Type\s*\|\s*Description\s*\|\s*Required\s*\|\s*Default\s*\|(.*?)(?:\n\||\n\n|\Z)'
        matches = re.findall(param_table_pattern, content, re.DOTALL | re.IGNORECASE)

        for match in matches:
            rows = match.strip().split('\n')
            for row in rows:
                if row.strip().startswith('|') and '---' not in row:
                    parts = [p.strip() for p in row.split('|')[1:-1]]
                    if len(parts) >= 4:
                        param_name, param_type, param_desc, param_required = parts[:4]
                        param_default = parts[4] if len(parts) > 4 else ""
                        parameters[param_name] = {
                            "type": param_type.lower().replace("string", "str").replace("number", "float"),
                            "description": param_desc,
                            "required": param_required.lower() in ("true", "yes", "sim"),
                            "default": param_default if param_default else None,
                        }

        return parameters

    def get_skill(self, name: str) -> Optional[OpenSquadSkill]:
        """Retorna uma skill pelo nome."""
        if not self._loaded:
            self.load_skills()
        return self._skills.get(name)

    def list_skills(self) -> List[OpenSquadSkill]:
        """Lista todas as skills carregadas."""
        if not self._loaded:
            self.load_skills()
        return list(self._skills.values())

    def get_action_schemas(self) -> List[Dict[str, Any]]:
        """Retorna schemas de todas as skills para function calling."""
        if not self._loaded:
            self.load_skills()
        return [skill.to_action_schema() for skill in self._skills.values()]

    async def execute_skill(self, skill_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Executa uma skill via script (Python/Node.js)."""
        skill = self.get_skill(skill_name)
        if not skill:
            return {"error": f"Skill não encontrada: {skill_name}"}

        # Tenta executar script principal
        main_script = skill.scripts.get("main") or skill.scripts.get("generate") or skill.scripts.get("run")

        if not main_script:
            return {"error": f"Nenhum script principal encontrado para skill: {skill_name}"}

        script_path = Path(main_script)

        # Se o path não é absoluto, resolve relativo ao diretório raiz do projeto
        if not script_path.is_absolute():
            # Assume que o script está no diretório skills do projeto
            script_path = Path.cwd() / script_path

        # Converte params para argumentos de linha de comando
            cmd_args = []
            for key, value in params.items():
                if isinstance(value, bool):
                    if value:
                        cmd_args.append(f"--{key}")
                elif value is not None:
                    cmd_args.append(f"--{key}")
                    cmd_args.append(str(value))

            try:
                if script_path.suffix == ".py":
                    # Executa script Python com argumentos
                    result = subprocess.run(
                        ["python", str(script_path)] + cmd_args,
                        capture_output=True,
                        text=True,
                        timeout=skill.timeout,
                        cwd=script_path.parent,
                    )
                elif script_path.suffix in (".js", ".ts"):
                    # Executa script Node.js com argumentos
                    result = subprocess.run(
                        ["node", str(script_path)] + cmd_args,
                        capture_output=True,
                        text=True,
                        timeout=skill.timeout,
                        cwd=script_path.parent,
                    )
                else:
                    return {"error": f"Tipo de script não suportado: {script_path.suffix}"}

                if result.returncode != 0:
                    return {"error": f"Script falhou: {result.stderr}", "stdout": result.stdout}

                # Tenta parsear JSON do stdout
                try:
                    return json.loads(result.stdout)
                except json.JSONDecodeError:
                    return {"output": result.stdout}

            except subprocess.TimeoutExpired:
                return {"error": "Timeout na execução da skill"}
            except Exception as e:
                return {"error": f"Erro ao executar skill: {e}"}


# Instância global
_skills_registry: Optional[OpenSquadSkillsRegistry] = None


def get_opensquad_registry(skills_dir: Path = None) -> OpenSquadSkillsRegistry:
    """Retorna instância global do registry."""
    global _skills_registry
    if _skills_registry is None:
        _skills_registry = OpenSquadSkillsRegistry(skills_dir)
    return _skills_registry


def init_opensquad_skills(skills_dir: Path = None) -> Dict[str, OpenSquadSkill]:
    """Inicializa e carrega todas as skills do OpenSquad."""
    registry = get_opensquad_registry(skills_dir)
    return registry.load_skills()


def get_opensquad_action_schemas() -> List[Dict[str, Any]]:
    """Retorna schemas de actions para integração com JARVIS."""
    registry = get_opensquad_registry()
    return registry.get_action_schemas()


async def execute_opensquad_skill(skill_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Executa uma skill do OpenSquad."""
    registry = get_opensquad_registry()
    return await registry.execute_skill(skill_name, params)


if __name__ == "__main__":
    # Teste rápido
    registry = OpenSquadSkillsRegistry(Path("skills"))
    skills = registry.load_skills()
    print(f"\nTotal de skills carregadas: {len(skills)}")
    for name, skill in skills.items():
        print(f"  - {name}: {skill.description}")
        print(f"    Scripts: {list(skill.scripts.keys())}")
        print(f"    Parâmetros: {list(skill.parameters.keys())}")
        print()