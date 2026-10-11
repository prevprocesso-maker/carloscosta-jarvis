from __future__ import annotations

import pytest
from jarvis.core.actions.registry import action, get_action, list_actions, get_openai_schemas, clear_registry, execute_action


def test_action_registry_basic():
    clear_registry()

    @action(namespace="test", description="Test action")
    def test_action(name: str, value: int = 42) -> dict:
        return {"name": name, "value": value}

    # Verifica registro
    entry = get_action("test.test_action")
    assert entry is not None
    assert entry["schema"].name == "test.test_action"
    assert entry["schema"].namespace == "test"
    assert "name" in entry["schema"].parameters["properties"]
    assert "value" in entry["schema"].parameters["properties"]
    assert entry["schema"].required == ["name"]

    # Verifica listagem
    actions = list_actions("test")
    assert len(actions) == 1
    assert actions[0].name == "test.test_action"

    # Verifica schema OpenAI
    schemas = get_openai_schemas("test")
    assert len(schemas) == 1
    assert schemas[0]["function"]["name"] == "test.test_action"
    assert schemas[0]["function"]["parameters"]["required"] == ["name"]


def test_action_execution():
    clear_registry()

    @action(namespace="math", description="Soma dois números")
    def add(a: int, b: int) -> dict:
        return {"result": a + b}

    result = execute_action("math.add", {"a": 2, "b": 3})
    assert result == {"result": 5}


def test_action_with_session():
    clear_registry()

    @action(namespace="ctx", description="Action com session")
    def with_session(text: str, session=None) -> dict:
        return {"text": text, "has_session": session is not None}

    class FakeSession:
        pass

    result = execute_action("ctx.with_session", {"text": "hello"}, session=FakeSession())
    assert result["has_session"] is True


def test_clear_registry():
    clear_registry()
    assert len(list_actions()) == 0