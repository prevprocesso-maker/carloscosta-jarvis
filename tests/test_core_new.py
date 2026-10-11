from __future__ import annotations

import pytest
import tempfile
import asyncio
from pathlib import Path

from jarvis_installer.core.telemetry import (
    Telemetry, Tracer, Logger, MetricsCollector, Span, LogEntry, LogLevel, SpanKind,
    get_telemetry, init_telemetry, trace
)
from jarvis_installer.core.guardrails import (
    GuardrailsEngine, GuardrailRule, GuardrailDecision, ApprovalRequest,
    RiskLevel, ActionCategory, get_guardrails, init_guardrails
)
from jarvis_installer.core.knowledge import (
    KnowledgeBase, Document, Chunk, SearchResult, DocumentType, ChunkStrategy,
    EmbeddingProvider, MockEmbeddingProvider, get_knowledge_base, init_knowledge_base
)
from jarvis_installer.core.versioning import (
    VersionManager, ReleaseManager, MigrationManager, Migration, Version,
    ReleaseChannel, ReleaseInfo, get_version_manager, get_release_manager,
    get_current_version, get_current_channel, bump_version
)


class TestTelemetry:
    @pytest.mark.asyncio
    async def test_telemetry_initialization(self):
        telemetry = Telemetry("test-service")
        await telemetry.start()
        assert telemetry._running
        await telemetry.shutdown()

    @pytest.mark.asyncio
    async def test_tracer_span(self):
        tracer = Tracer()
        async with tracer.start_span("test_span") as span:
            assert span.name == "test_span"
            assert span.kind == SpanKind.INTERNAL
        assert span.end_time is not None
        assert span.status == "OK"

    @pytest.mark.asyncio
    async def test_tracer_nested_spans(self):
        tracer = Tracer()
        async with tracer.start_span("parent") as parent:
            async with tracer.start_span("child", parent=parent) as child:
                assert child.parent_span_id == parent.span_id
            assert parent.status == "OK"

    def test_logger(self):
        logger = Logger("test")
        logger.info("test message", key="value")
        logs = logger.get_logs()
        assert len(logs) == 1
        assert logs[0].message == "test message"
        assert logs[0].attributes["key"] == "value"

    def test_metrics_collector(self):
        metrics = MetricsCollector()
        metrics.increment("requests", labels={"endpoint": "/api"})
        metrics.increment("requests", labels={"endpoint": "/api"})
        metrics.set_gauge("active_users", 42)
        metrics.observe("latency", 0.123, labels={"endpoint": "/api"})

        data = metrics.get_metrics()
        assert data["counters"]["requests{endpoint=\"/api\"}"] == 2.0
        assert data["gauges"]["active_users"] == 42.0
        assert data["histograms"]["latency{endpoint=\"/api\"}"]["count"] == 1


class TestGuardrails:
    def test_default_rules_loaded(self):
        guardrails = GuardrailsEngine()
        assert len(guardrails.rules) >= 10  # At least default rules

    def test_check_action_allowed(self):
        guardrails = GuardrailsEngine()
        decision = guardrails.check_action("read_file", {"path": "/home/user/file.txt"})
        assert decision.allowed
        assert decision.risk_level.value == "safe"

    def test_check_action_blocked(self):
        guardrails = GuardrailsEngine()
        decision = guardrails.check_action("delete_file", {"path": "/windows/system32/file.dll"})
        assert not decision.allowed
        assert decision.risk_level == "critical"

    def test_check_action_requires_approval(self):
        guardrails = GuardrailsEngine()
        decision = guardrails.check_action("write_file", {"path": "/etc/passwd"})
        assert not decision.allowed
        assert decision.requires_approval
        assert decision.approval_id is not None

    def test_approval_workflow(self):
        guardrails = GuardrailsEngine()
        decision = guardrails.check_action("sudo_cmd", {"cmd": "sudo reboot"})
        assert decision.requires_approval
        approval_id = decision.approval_id

        # Approve
        assert guardrails.approve(approval_id, "admin")
        approval = guardrails.approvals[approval_id]
        assert approval.status == "approved"
        assert approval.approved_by == "admin"

    def test_custom_rule(self):
        guardrails = GuardrailsEngine()
        custom_rule = GuardrailRule(
            id="custom_test",
            name="Custom Test Rule",
            description="Test custom rule",
            category=ActionCategory.FILE_WRITE,
            risk_level=RiskLevel.HIGH,
            pattern=r"test_pattern",
            action="warn",
            message="Custom warning",
        )
        guardrails.add_rule(custom_rule)
        decision = guardrails.check_action("write", {"path": "test_pattern.txt"})
        assert decision.allowed  # warn allows
        assert decision.risk_level == "high"


class TestKnowledgeBase:
    @pytest.mark.asyncio
    async def test_add_document(self):
        kb = KnowledgeBase(embedding_provider=MockEmbeddingProvider())
        await kb.initialize()

        doc = await kb.add_document(
            title="Test Doc",
            content="This is a test document about JARVIS.",
            doc_type=DocumentType.TEXT,
            source="test",
            tags=["test", "jarvis"],
        )
        assert doc.id is not None
        assert doc.title == "Test Doc"
        assert "jarvis" in doc.tags

    @pytest.mark.asyncio
    async def test_search(self):
        kb = KnowledgeBase(embedding_provider=MockEmbeddingProvider())
        await kb.initialize()

        await kb.add_document("Doc 1", "JARVIS is a voice assistant", source="test1")
        await kb.add_document("Doc 2", "Python is a programming language", source="test2")

        results = await kb.search("JARVIS", limit=5)
        assert len(results) > 0
        assert any("JARVIS" in r.snippet for r in results)

    @pytest.mark.asyncio
    async def test_ask_rag(self):
        kb = KnowledgeBase(embedding_provider=MockEmbeddingProvider())
        await kb.initialize()

        await kb.add_document("JARVIS Info", "JARVIS is an AI assistant with voice control", source="kb")

        result = await kb.ask("What is JARVIS?")
        assert "question" in result
        assert "context" in result
        assert "sources" in result
        assert len(result["sources"]) > 0

    @pytest.mark.asyncio
    async def test_chunking_strategies(self):
        kb = KnowledgeBase(chunk_strategy=ChunkStrategy.FIXED_SIZE, chunk_size=100, overlap=20)
        await kb.initialize()

        long_text = "A" * 500
        doc = await kb.add_document("Long Doc", long_text, source="test")
        chunks = kb.store.get_chunks(doc.id)
        assert len(chunks) > 1  # Should be chunked


class TestVersioning:
    def test_version_parsing(self):
        v1 = parse_version("1.2.3")
        assert v1.major == 1 and v1.minor == 2 and v1.patch == 3

        v2 = parse_version("1.2.3-beta")
        assert v2.channel == "beta"

        v3 = parse_version("1.2.3+100")
        assert v3.build == 100

    def test_version_comparison(self):
        v1 = Version(1, 0, 0)
        v2 = Version(1, 1, 0)
        v3 = Version(2, 0, 0, channel="beta")

        assert v1 < v2
        assert v2 < v3

    def test_version_bump(self):
        v = Version(1, 2, 3)
        v_major = v.bump(VersionComponent.MAJOR)
        assert v_major == Version(2, 0, 0)

        v_minor = v.bump(VersionComponent.MINOR)
        assert v_minor == Version(1, 3, 0)

        v_patch = v.bump(VersionComponent.PATCH)
        assert v_patch == Version(1, 2, 4)

    def test_version_string_format(self):
        v = Version(1, 2, 3, channel=ReleaseChannel.BETA, build=100)
        assert str(v) == "1.2.3-beta+100"

    @pytest.mark.asyncio
    async def test_migration_manager(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            migrations_dir = Path(tmpdir) / "migrations"
            migrations_dir.mkdir()

            # Create test migration
            migration_file = migrations_dir / "001_test.py"
            migration_file.write_text('''
migration = Migration(
    version="1.0.0",
    name="Test Migration",
    description="Test",
    up=lambda c: {**c, "migrated": True},
    down=lambda c: {k: v for k, v in c.items() if k != "migrated"},
)
''')

            manager = MigrationManager(migrations_dir)
            assert len(manager.migrations) == 1

            config = {"version": "0.0.0"}
            migrated = manager.migrate(config)
            assert migrated.get("migrated") is True
            assert migrated["version"] == "1.0.0"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])