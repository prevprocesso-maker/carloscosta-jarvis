"""Smoke tests for VaultRouter."""
import pytest
from pathlib import Path
from jarvis.integrations import VaultRouter, IntentClassifier, IntentType


@pytest.fixture
def vault_path():
    return Path(r"C:\Users\NIKE\Downloads\Jarvis-obsidian-teste")


@pytest.fixture
def router(vault_path):
    return VaultRouter(vault_path)


@pytest.fixture
def classifier():
    return IntentClassifier()


def test_vault_router_meeting_client(router, classifier):
    """Test routing meeting with company -> goes to reunioes if no client folder."""
    intent = classifier.classify_intent("reunião amanhã com João da Acme")
    assert intent.type == IntentType.CREATE_MEETING
    route = router.route(intent)
    assert route["action"] == "create_meeting"
    # Since there's no clientes/acme folder, it goes to reunioes/acme/
    assert "reunioes/acme" in route["folder"]


def test_vault_router_meeting_internal(router, classifier):
    """Test routing meeting without company -> reunioes."""
    intent = classifier.classify_intent("reunião amanhã com Maria")
    assert intent.type == IntentType.CREATE_MEETING
    route = router.route(intent)
    assert route["action"] == "create_meeting"
    assert "reunioes/maria" in route["folder"]


def test_vault_router_idea(router, classifier):
    """Test routing idea."""
    intent = classifier.classify_intent("ideia para um reel sobre produtividade")
    assert intent.type == IntentType.CREATE_IDEA
    route = router.route(intent)
    assert route["action"] == "create_idea"
    assert "conteudo/ideias" in route["folder"]


def test_vault_router_lead(router, classifier):
    """Test routing lead."""
    intent = classifier.classify_intent("novo lead João da Acme")
    assert intent.type == IntentType.CREATE_LEAD
    route = router.route(intent)
    assert route["action"] == "create_lead"
    assert "pessoas" in route["folder"]