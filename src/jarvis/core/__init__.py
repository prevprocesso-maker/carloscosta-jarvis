from __future__ import annotations

from .system import SystemInfo, detect_system, check_python_version, get_package_manager, install_system_deps
from .paths import JARVISPaths, get_jarvis_paths, get_default_base_dir, get_python_executable, get_pip_executable, ensure_dirs, get_installer_path, get_templates_dir, get_scripts_dir
from .version import VersionInfo, Channel, parse_version, format_version, compare_versions, is_newer, get_current_version, get_current_channel, ReleaseInfo
from .config import DEFAULT_CONFIG, load_config, save_config, merge_configs, get_config_value, set_config_value, migrate_config, ensure_config_migrated, register_migration, Settings, get_settings
from .health import HealthResult, HealthReport, HealthChecker, run_health_check
from .security import SignatureResult, compute_sha256, verify_checksum, verify_signature, generate_checksums, save_checksums, load_checksums, verify_all_checksums, sign_file
from .telemetry import Telemetry, Tracer, Logger, MetricsCollector, Span, LogEntry, LogLevel, SpanKind, get_telemetry, init_telemetry, trace
from .guardrails import GuardrailsEngine, GuardrailRule, GuardrailDecision, ApprovalRequest, RiskLevel, ActionCategory, get_guardrails, init_guardrails
from .knowledge import KnowledgeBase, Document, Chunk, SearchResult, DocumentType, ChunkStrategy, EmbeddingProvider, MockEmbeddingProvider, get_knowledge_base, init_knowledge_base
from .versioning import VersionManager, ReleaseManager, MigrationManager, Migration, Version, ReleaseChannel, ReleaseInfo, get_version_manager, get_release_manager, get_current_version, get_current_channel, bump_version

__all__ = [
    # system
    "SystemInfo", "detect_system", "check_python_version", "get_package_manager", "install_system_deps",
    # paths
    "JARVISPaths", "get_jarvis_paths", "get_default_base_dir", "get_python_executable", "get_pip_executable", "ensure_dirs", "get_installer_path", "get_templates_dir", "get_scripts_dir",
    # version (legacy)
    "VersionInfo", "Channel", "parse_version", "format_version", "compare_versions", "is_newer", "get_current_version", "get_current_channel", "ReleaseInfo",
    # config
    "DEFAULT_CONFIG", "load_config", "save_config", "merge_configs", "get_config_value", "set_config_value", "migrate_config", "ensure_config_migrated", "register_migration",
    # health
    "HealthResult", "HealthReport", "HealthChecker", "run_health_check",
    # security
    "SignatureResult", "compute_sha256", "verify_checksum", "verify_signature", "generate_checksums", "save_checksums", "load_checksums", "verify_all_checksums", "sign_file",
    # telemetry
    "Telemetry", "Tracer", "Logger", "MetricsCollector", "Span", "LogEntry", "LogLevel", "SpanKind", "get_telemetry", "init_telemetry", "trace",
    # guardrails
    "GuardrailsEngine", "GuardrailRule", "GuardrailDecision", "ApprovalRequest", "RiskLevel", "ActionCategory", "get_guardrails", "init_guardrails",
    # knowledge
    "KnowledgeBase", "Document", "Chunk", "SearchResult", "DocumentType", "ChunkStrategy", "EmbeddingProvider", "MockEmbeddingProvider", "get_knowledge_base", "init_knowledge_base",
    # versioning
    "VersionManager", "ReleaseManager", "MigrationManager", "Migration", "Version", "ReleaseChannel", "ReleaseInfo", "get_version_manager", "get_release_manager", "get_current_version", "get_current_channel", "bump_version",
]