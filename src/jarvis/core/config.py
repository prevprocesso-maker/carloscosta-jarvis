from __future__ import annotations

import os
import yaml
from pathlib import Path
from typing import Any, Dict, Optional
from copy import deepcopy

from .version import VersionInfo, parse_version, CURRENT_VERSION, CURRENT_CHANNEL


DEFAULT_CONFIG = {
    "app": {
        "name": "jarvis",
        "version": CURRENT_VERSION,
        "channel": "nightly",
        "install_date": None,
        "install_id": None,
    },
    "user": {
        "name": "Raphael",
        "language": "pt-BR",
        "timezone": "America/Sao_Paulo",
    },
    "gemini": {
        "api_key": "",
        "fast_model": "gemini-3.5-flash-lite",
        "fast_fallback_model": "gemini-3.1-flash-lite",
        "reserve_model": "gemini-2.5-flash",
        "live_model": "gemini-2.5-flash-native-audio-preview",
        "live_voice": "Charon",
    },
    "transports": {
        "local": {
            "enabled": True,
            "whisper_model": "large-v3",
            "whisper_device": "auto",
            "vad_threshold": 0.09,
            "sample_rate": 16000,
            "chunk_size": 1024,
        },
        "remote": {
            "enabled": True,
            "host": "0.0.0.0",
            "port": 8000,
            "token": "",
            "tls": {
                "enabled": False,
                "cert_path": "",
                "key_path": "",
            },
        },
        "webhook": {
            "enabled": True,
            "whatsapp_port": 8765,
        },
    },
    "ui": {
        "desktop_enabled": True,
        "web_enabled": True,
        "theme": "stark",
    },
    "update": {
        "channel": "nightly",
        "auto_check": True,
        "auto_apply": False,
        "check_interval_hours": 6,
        "backup_retention_days": 30,
        "rollback_on_health_failure": True,
        "health_check_timeout_seconds": 30,
    },
    "deploy": {
        "docker_enabled": False,
        "traefik_enabled": False,
        "domain": "",
        "letsencrypt_email": "",
    },
    "logging": {
        "level": "INFO",
        "rotation": "daily",
        "retention_days": 7,
        "format": "json",
    },
"knowledge": {
        "embedding_model": "sentence-transformers/all-MiniLM-L6-v2",
        "embedding_device": "auto",
        "chunk_size": 512,
        "chunk_overlap": 50,
        "chunk_strategy": "recursive",
        "search_threshold": 0.25,
        "search_limit": 10,
        "hybrid_search": True,
        "rerank": False,
    },
}


def load_config(config_path: Path) -> Dict[str, Any]:
    """Carrega configuração do arquivo YAML."""
    if not config_path.exists():
        return deepcopy(DEFAULT_CONFIG)
    
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return merge_configs(DEFAULT_CONFIG, data)
    except Exception:
        return deepcopy(DEFAULT_CONFIG)


def save_config(config: Dict[str, Any], config_path: Path) -> None:
    """Salva configuração no arquivo YAML."""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, sort_keys=False, default_flow_style=False)


def merge_configs(default: Dict, user: Dict) -> Dict:
    """Merge recursivo de configurações (user sobrescreve default)."""
    result = deepcopy(default)
    for key, value in user.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = merge_configs(result[key], value)
        else:
            result[key] = value
    return result


def get_config_value(config: Dict, path: str, default: Any = None) -> Any:
    """Obtém valor aninhado via path (ex: 'gemini.api_key')."""
    keys = path.split(".")
    current = config
    for key in keys:
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            return default
    return current


def set_config_value(config: Dict, path: str, value: Any) -> None:
    """Define valor aninhado via path."""
    keys = path.split(".")
    current = config
    for key in keys[:-1]:
        if key not in current or not isinstance(current[key], dict):
            current[key] = {}
        current = current[key]
    current[keys[-1]] = value


# MIGRAÇÕES
# Cada migração recebe config e retorna config migrado
MIGRATIONS = {}


def register_migration(from_version: str):
    """Decorator para registrar migração."""
    def decorator(func):
        MIGRATIONS[from_version] = func
        return func
    return decorator


@register_migration("1.0.0")
def migrate_1_0_0(config: Dict) -> Dict:
    """Migração v1.0.0 -> v2.0.0: Nova estrutura de transports."""
    # Move configurações antigas para nova estrutura
    if "stt" in config:
        config.setdefault("transports", {}).setdefault("local", {})
        config["transports"]["local"]["whisper_model"] = config["stt"].get("model", "large-v3")
        del config["stt"]
    if "tts" in config:
        config.setdefault("transports", {}).setdefault("local", {})
        config["transports"]["local"]["tts_engine"] = config["tts"].get("engine", "gemini_live")
        del config["tts"]
    if "brain" in config:
        # Brain config movida para transports.remote ou mantida em brain
        pass
    return config


@register_migration("2.0.0")
def migrate_2_0_0(config: Dict) -> Dict:
    """Migração v2.0.0 -> v2.1.0: Novos campos de update."""
    config.setdefault("update", {}).setdefault("health_check_timeout_seconds", 30)
    config.setdefault("update", {}).setdefault("rollback_on_health_failure", True)
    config.setdefault("deploy", {}).setdefault("traefik_enabled", False)
    return config


def migrate_config(config: Dict, from_version: str, to_version: str = CURRENT_VERSION) -> Dict:
    """Aplica migrações sequenciais."""
    from_ver = parse_version(from_version)
    to_ver = parse_version(to_version)
    
    if from_ver >= to_ver:
        return config
    
    # Ordena migrações por versão
    migrations = sorted(MIGRATIONS.items(), key=lambda x: parse_version(x[0]))
    
    for version_str, migration_fn in migrations:
        ver = parse_version(version_str)
        if from_ver < ver <= to_ver:
            config = migration_fn(config)
    
    return config


def ensure_config_migrated(config_path: Path) -> Dict[str, Any]:
    """Carrega config e aplica migrações se necessário."""
    config = load_config(config_path)
    current_version = config.get("app", {}).get("version", "1.0.0")
    
    if current_version != CURRENT_VERSION:
        print(f"[Config] Migrando configuração de {current_version} para {CURRENT_VERSION}")
        config = migrate_config(config, current_version, CURRENT_VERSION)
        config["app"]["version"] = CURRENT_VERSION
        save_config(config, config_path)
        print("[Config] Migração concluída")
    
    return config


def get_settings() -> Dict[str, Any]:
    """Carrega e retorna as configurações do JARVIS."""
    from .paths import get_jarvis_paths
    paths = get_jarvis_paths()
    return ensure_config_migrated(paths.config_file)


class Settings:
    """Wrapper para acessar configurações como atributos."""
    
    def __init__(self, config: Dict[str, Any]):
        self._config = config
    
    def __getattr__(self, name: str) -> Any:
        if name in self._config:
            value = self._config[name]
            if isinstance(value, dict):
                return Settings(value)
            return value
        raise AttributeError(f"'Settings' object has no attribute '{name}'")
    
    def __getitem__(self, key: str) -> Any:
        return self._config[key]
    
    def get(self, key: str, default: Any = None) -> Any:
        return self._config.get(key, default)
    
    def __contains__(self, key: str) -> bool:
        return key in self._config
    
    def to_dict(self) -> Dict[str, Any]:
        return self._config


# Global settings instance
_settings_instance: Optional[Settings] = None


def get_settings() -> Settings:
    """Carrega e retorna as configurações do JARVIS como objeto Settings."""
    global _settings_instance
    if _settings_instance is None:
        from .paths import get_jarvis_paths
        paths = get_jarvis_paths()
        config = ensure_config_migrated(paths.config_file)
        _settings_instance = Settings(config)
    return _settings_instance


# Exports for backward compatibility
settings = get_settings()

__all__ = [
    "DEFAULT_CONFIG", "load_config", "save_config", "merge_configs",
    "get_config_value", "set_config_value", "migrate_config",
    "ensure_config_migrated", "register_migration",
    "Settings", "get_settings", "settings",
]