from __future__ import annotations

from .python_env import create_venv, install_jarvis_package, verify_installation, upgrade_pip
from .node_env import check_node_version, install_node_deps, build_frontend, ensure_node_available, backup_frontend_build, restore_frontend_build
from .docker_env import check_docker_available, check_docker_compose_available, build_docker_image, docker_compose_up, docker_compose_down
from .shortcuts import create_desktop_shortcut, create_start_menu_entry, remove_shortcuts
from .service import install_systemd_service, install_nssm_service, install_launchd_service, uninstall_service, get_service_status, start_service, stop_service, restart_service

__all__ = [
    # python_env
    "create_venv", "install_jarvis_package", "verify_installation", "upgrade_pip",
    # node_env
    "check_node_version", "install_node_deps", "build_frontend", "ensure_node_available", "backup_frontend_build", "restore_frontend_build",
    # docker_env
    "check_docker_available", "check_docker_compose_available", "build_docker_image", "docker_compose_up", "docker_compose_down",
    # shortcuts
    "create_desktop_shortcut", "create_start_menu_entry", "remove_shortcuts",
    # service
    "install_systemd_service", "install_nssm_service", "install_launchd_service", "uninstall_service", "get_service_status", "start_service", "stop_service", "restart_service",
]