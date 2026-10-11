from __future__ import annotations

from .system import SystemInfo, detect_system, check_python_version, get_package_manager, install_system_deps
from .paths import JARVISPaths, get_jarvis_paths, get_default_base_dir, get_python_executable, get_pip_executable, ensure_dirs, get_installer_path, get_templates_dir, get_scripts_dir
from .version import VersionInfo, Channel, parse_version, format_version, compare_versions, is_newer, get_current_version, get_current_channel, ReleaseInfo
from .config import DEFAULT_CONFIG, load_config, save_config, merge_configs, get_config_value, set_config_value, migrate_config, ensure_config_migrated, register_migration
from .health import HealthResult, HealthReport, HealthChecker, run_health_check
from .security import SignatureResult, compute_sha256, verify_checksum, verify_signature, generate_checksums, save_checksums, load_checksums, verify_all_checksums, sign_file

__all__ = [
    # system
    "SystemInfo", "detect_system", "check_python_version", "get_package_manager", "install_system_deps",
    # paths
    "JARVISPaths", "get_jarvis_paths", "get_default_base_dir", "get_python_executable", "get_pip_executable", "ensure_dirs", "get_installer_path", "get_templates_dir", "get_scripts_dir",
    # version
    "VersionInfo", "Channel", "parse_version", "format_version", "compare_versions", "is_newer", "get_current_version", "get_current_channel", "ReleaseInfo",
    # config
    "DEFAULT_CONFIG", "load_config", "save_config", "merge_configs", "get_config_value", "set_config_value", "migrate_config", "ensure_config_migrated", "register_migration",
    # health
    "HealthResult", "HealthReport", "HealthChecker", "run_health_check",
    # security
    "SignatureResult", "compute_sha256", "verify_checksum", "verify_signature", "generate_checksums", "save_checksums", "load_checksums", "verify_all_checksums", "sign_file",
]