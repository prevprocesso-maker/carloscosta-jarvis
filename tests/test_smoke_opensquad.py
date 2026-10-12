"""Smoke tests for OpenSquadSkillsRegistry."""
import pytest
from jarvis.integrations import (
    init_opensquad_skills,
    get_opensquad_action_schemas,
    OpenSquadSkillsRegistry,
)


def test_opensquad_skills_load():
    skills = init_opensquad_skills()
    assert len(skills) >= 10  # We have 11 skills
    expected = {
        "apify", "blotato", "canva", "image-ai-generator",
        "image-creator", "image-fetcher", "instagram-publisher",
        "Best-Practice Creator", "opensquad-skill-creator",
        "resend", "template-designer"
    }
    assert set(skills.keys()) == expected


def test_opensquad_action_schemas():
    schemas = get_opensquad_action_schemas()
    assert len(schemas) >= 10
    for schema in schemas:
        assert schema["type"] == "function"
        assert "name" in schema["function"]
        assert "description" in schema["function"]
        assert "parameters" in schema["function"]


def test_opensquad_skill_timeout_default():
    skills = init_opensquad_skills()
    for skill in skills.values():
        assert hasattr(skill, "timeout")
        assert isinstance(skill.timeout, int)
        assert skill.timeout > 0
        assert skill.timeout <= 300  # Reasonable max


def test_opensquad_registry_singleton():
    """Test that registry is a singleton."""
    reg1 = OpenSquadSkillsRegistry()
    reg2 = OpenSquadSkillsRegistry()
    # Both should share the same loaded skills
    reg1.load_skills()
    reg2.load_skills()
    assert reg1._skills is reg2._skills