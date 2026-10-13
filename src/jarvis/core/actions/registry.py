from __future__ import annotations

import inspect
from functools import wraps
from typing import get_type_hints, Callable, Any, Optional
from pydantic import BaseModel, create_model, Field
import json


_action_registry: dict[str, dict] = {}
_namespace_order: list[str] = []


class ActionSchema(BaseModel):
    name: str
    description: str
    namespace: str
    parameters: dict = Field(default_factory=dict)
    required: list[str] = Field(default_factory=list)


def action(
    name: Optional[str] = None,
    namespace: str = "general",
    description: str = "",
):
    """Decorator para registrar actions automaticamente.

    Uso:
        @action(namespace="computer", description="Abre um aplicativo")
        def open_app(name: str) -> dict:
            ...

    O schema OpenAI é gerado automaticamente dos type hints + docstring.
    """
    def decorator(func: Callable) -> Callable:
        nonlocal name
        action_name = name or func.__name__
        full_name = f"{namespace}.{action_name}"

        sig = inspect.signature(func)
        hints = get_type_hints(func)

        fields = {}
        required = []

        for param_name, param in sig.parameters.items():
            if param_name in ("self", "cls", "session", "context"):
                continue

            param_type = hints.get(param_name, str)
            default = param.default

            if default is param.empty:
                required.append(param_name)
                fields[param_name] = (param_type, ...)
            else:
                fields[param_name] = (param_type, default)

        if fields:
            ArgsModel = create_model(f"{full_name}_Args", **fields)
            schema = ArgsModel.model_json_schema()
            parameters = {
                "type": "object",
                "properties": schema.get("properties", {}),
                "required": required,
            }
        else:
            parameters = {"type": "object", "properties": {}, "required": []}

        action_schema = ActionSchema(
            name=full_name,
            description=description or func.__doc__ or "",
            namespace=namespace,
            parameters=parameters,
            required=required,
        )

        _action_registry[full_name] = {
            "func": func,
            "schema": action_schema,
        }

        if namespace not in _namespace_order:
            _namespace_order.append(namespace)

        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            if inspect.iscoroutinefunction(func):
                return await func(*args, **kwargs)
            return func(*args, **kwargs)

        return async_wrapper

    return decorator


def get_action(name: str) -> Optional[dict]:
    return _action_registry.get(name)


def list_actions(namespace: Optional[str] = None) -> list[ActionSchema]:
    if namespace:
        return [a["schema"] for a in _action_registry.values() if a["schema"].namespace == namespace]
    return [a["schema"] for a in _action_registry.values()]


def get_openai_schemas(namespace: Optional[str] = None) -> list[dict]:
    """Retorna schemas no formato OpenAI function calling."""
    schemas = []
    for a in list_actions(namespace):
        schemas.append({
            "type": "function",
            "function": {
                "name": a.name,
                "description": a.description,
                "parameters": a.parameters,
            }
        })
    return schemas


def get_namespaces() -> list[str]:
    return _namespace_order.copy()


def clear_registry() -> None:
    """Útil para testes."""
    _action_registry.clear()
    _namespace_order.clear()


def execute_action(name: str, args: dict, session: Any = None) -> Any:
    """Executa uma action registrada pelo nome."""
    entry = _action_registry.get(name)
    if not entry:
        raise ValueError(f"Action not found: {name}")

    func = entry["func"]
    sig = inspect.signature(func)

    # Injeta session/context se a função aceitar
    call_kwargs = args.copy()
    if "session" in sig.parameters:
        call_kwargs["session"] = session
    if "context" in sig.parameters:
        call_kwargs["context"] = session

    return func(**call_kwargs)


def action_to_dict(action_schema: ActionSchema) -> dict:
    return {
        "name": action_schema.name,
        "description": action_schema.description,
        "namespace": action_schema.namespace,
        "parameters": action_schema.parameters,
    }


def export_registry() -> dict:
    """Exporta registry completo para debug/persistência."""
    return {
        "actions": [action_to_dict(s) for s in list_actions()],
        "namespaces": get_namespaces(),
    }


def register_opensquad_skills(opensquad_schemas: list[dict]) -> int:
    """Registra skills do OpenSquad como actions no registry.
    
    Args:
        opensquad_schemas: Lista de schemas no formato OpenAI function calling
            (com keys 'type' e 'function' contendo name, description, parameters)
        
    Returns:
        Número de skills registradas
    """
    count = 0
    for schema in opensquad_schemas:
        # Handle both formats: OpenAI format (type + function) and raw format
        if schema.get("type") == "function" and "function" in schema:
            func = schema["function"]
            name = func.get("name", "")
            description = func.get("description", "")
            parameters = func.get("parameters", {})
        else:
            name = schema.get("name", "")
            description = schema.get("description", "")
            parameters = schema.get("parameters", {})
        
        if not name or name in _action_registry:
            continue
            
        # Cria função wrapper que executa a skill via OpenSquad bridge
        async def make_skill_func(skill_name: str, **kwargs):
            from jarvis.integrations import execute_opensquad_skill
            return await execute_opensquad_skill(skill_name, kwargs)
        
        # Registra usando o decorator action
        @action(name=name, namespace="opensquad", description=description)
        async def registered_skill(**kwargs):
            skill_name = name  # Captura o nome atual
            return await make_skill_func(skill_name, **kwargs)
        
        count += 1
    
    return count