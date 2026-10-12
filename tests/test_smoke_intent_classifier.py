"""Smoke tests for IntentClassifier."""
import pytest
from jarvis.integrations import IntentClassifier, IntentType


def test_intent_classifier_meeting():
    clf = IntentClassifier()
    intent = clf.classify_intent("reunião amanhã com João da Acme às 15h")
    assert intent.type == IntentType.CREATE_MEETING
    assert intent.params.get("who") == "João da"
    assert intent.params.get("company") == "Acme"
    assert intent.params.get("when") == "tomorrow"
    assert intent.params.get("time") == "15h"


def test_intent_classifier_idea():
    clf = IntentClassifier()
    intent = clf.classify_intent("ideia para um reel sobre produtividade")
    assert intent.type == IntentType.CREATE_IDEA
    assert "reel" in intent.params.get("topic", "") or "produtividade" in intent.params.get("topic", "")


def test_intent_classifier_lead():
    clf = IntentClassifier()
    intent = clf.classify_intent("novo lead João da Acme")
    assert intent.type == IntentType.CREATE_LEAD
    assert intent.params.get("name") == "João da"