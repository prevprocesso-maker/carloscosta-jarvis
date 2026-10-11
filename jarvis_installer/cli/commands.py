from __future__ import annotations

import asyncio
import typer
from typing import Optional, List
from pathlib import Path
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.panel import Panel
from rich.text import Text
from rich.prompt import Prompt, Confirm
from rich import print as rprint

from ..paths import get_jarvis_paths, get_default_base_dir
from ..core.system import detect_system, check_python_version
from ..core.config import DEFAULT_CONFIG, load_config, save_config, ensure_config_migrated
from ..core.health import run_health_check
from ..core.version import get_current_version, get_current_channel, Channel
from ..install.python_env import create_venv, install_jarvis_package, verify_installation, upgrade_pip
from ..install.node_env import check_node_version, install_node_deps, build_frontend, ensure_node_available
from ..install.docker_env import check_docker_available, check_docker_compose_available
from ..install.shortcuts import create_desktop_shortcut, create_start_menu_entry, remove_shortcuts
from ..install.service import install_systemd_service, install_nssm_service, install_launchd_service, uninstall_service, get_service_status, start_service, stop_service, restart_service
from ..update.checker import UpdateChecker
from ..update.downloader import Downloader
from ..update.applier import apply_update
from ..update.rollback import RollbackManager
from ..update.notifier import UpdateNotifier
from ..paths import get_jarvis_paths, get_installer_path

app = typer.Typer(
    name="jarvis-installer",
    help="JARVIS CyberBot Auto-Installer & Updater",
    rich_markup_mode="rich",
    no_args_is_help=True,
)

console = Console()

# Global state
verbose = False


def get_version_ctx() -> dict:
    from ..core.version import get_current_version, get_current_channel
    return {
        "version": str(get_current_version()),
        "channel": get_current_channel().value,
    }


@app.callback()
def main(
    ctx: typer.Context,
    verbose_flag: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
    config: Optional[Path] = typer.Option(None, "--config", "-c", help="Config file path"),
):
    global verbose
    verbose = verbose_flag
    ctx.obj = {"config_path": config}


# ==================== INSTALL ====================

@app.command("install")
def install_cmd(
    channel: str = typer.Option("stable", "--channel", help="Release channel: stable, beta, nightly"),
    headless: bool = typer.Option(False, "--headless", help="Server install (no desktop shortcuts)"),
    docker: bool = typer.Option(False, "--docker", help="Install with Docker"),
    path: Optional[Path] = typer.Option(None, "--path", help="Install directory"),
    auto_yes: bool = typer.Option(False, "--yes", "-y", help="Auto-confirm all prompts"),
):
    """Instala JARVIS completo (backend + frontend + serviços)."""
    asyncio.run(_install_async(channel, headless, docker, path, auto_yes))


async def _install_async(channel: str, headless: bool, docker: bool, path: Optional[Path], auto_yes: bool):
    console.print(Panel.fit(
        "[bold cyan]JARVIS CyberBot Installer[/bold cyan]\n"
        f"Version: [green]{get_version_ctx()['version']}[/green] | Channel: [yellow]{channel}[/yellow]",
        border_style="cyan"
    ))
    
    # System check
    with console.status("[bold]Checking system requirements..."):
        system = detect_system()
        if not check_python_version("3.11"):
            console.print("[red]Python 3.11+ required[/red]")
            raise typer.Exit(1)
        
        console.print(f"[green]✓[/green] Python {system.python_version} ({system.arch})")
        console.print(f"[green]✓[/green] OS: {system.os} {system.os_version}")
        if system.has_gpu:
            console.print(f"[green]✓[/green] GPU: {system.gpu_name} ({system.gpu_memory_mb} MB)")
        if system.cuda_available:
            console.print(f"[green]✓[/green] CUDA: {system.cuda_version}")
    
    # Install path
    install_path = path or get_default_base_dir()
    if not auto_yes:
        install_path = Path(Prompt.ask("Install directory", default=str(install_path)))
    
    console.print(f"[cyan]Installing to: {install_path}[/cyan]")
    
    # Get paths
    from ..paths import get_jarvis_paths
    paths = get_jarvis_paths(install_path)
    
    # 1. Create venv
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Creating virtual environment...", total=None)
        if not create_venv(paths.venv):
            console.print("[red]Failed to create venv[/red]")
            raise typer.Exit(1)
        progress.update(task, completed=True)
    
    # 2. Upgrade pip
    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
        task = progress.add_task("Upgrading pip...", total=None)
        upgrade_pip(paths.venv)
        progress.update(task, completed=True)
    
    # 3. Install JARVIS package
    source_path = get_installer_path().parent  # Project root
    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
        task = progress.add_task("Installing JARVIS package...", total=None)
        if not install_jarvis_package(paths.venv, source_path):
            console.print("[red]Failed to install JARVIS package[/red]")
            raise typer.Exit(1)
        progress.update(task, completed=True)
    
    # 4. Verify installation
    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
        task = progress.add_task("Verifying installation...", total=None)
        if not verify_installation(paths.venv):
            console.print("[red]Installation verification failed[/red]")
            raise typer.Exit(1)
        progress.update(task, completed=True)
    
    # 5. Node.js + Frontend
    if not ensure_node_available("18"):
        console.print("[yellow]Node.js not found, attempting to install...[/yellow]")
        # ensure_node_available already tries to install
    
    if check_node_version("18"):
        console.print("[green]✓[/green] Node.js available")
        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
            task = progress.add_task("Installing frontend dependencies...", total=None)
            install_node_deps(paths.frontend)
            progress.update(task, completed=True)
        
        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
            task = progress.add_task("Building frontend...", total=None)
            build_frontend(paths.frontend)
            progress.update(task, completed=True)
    else:
        console.print("[yellow]Node.js not available, skipping frontend build[/yellow]")
    
    # 6. Config
    console.print("[cyan]Creating configuration...[/cyan]")
    from ..core.config import DEFAULT_CONFIG
    config = DEFAULT_CONFIG.copy()
    config["app"]["channel"] = channel
    config["app"]["install_date"] = __import__("datetime").datetime.now().isoformat()
    config["app"]["install_id"] = __import__("uuid").uuid4().hex
    save_config(DEFAULT_CONFIG, paths.config_file)
    
    # 7. Shortcuts & Services
    if not headless and not docker:
        # Shortcuts
        create_desktop_shortcut("JARVIS", paths.venv / "Scripts" / "python.exe" if platform.system().lower() == "windows" else paths.venv / "bin" / "python")
        create_start_menu_entry("JARVIS", paths.venv / "Scripts" / "python.exe" if platform.system().lower() == "windows" else paths.venv / "bin" / "python")
        
        # Service
        import platform
        system = platform.system().lower()
        if system == "linux":
            install_systemd_service()
        elif system == "windows":
            install_nssm_service()
        elif system == "darwin":
            install_launchd_service()
    
    # Docker
    if docker:
        console.print("[cyan]Docker installation not fully implemented yet[/cyan]")
    
    # Final
    console.print(Panel.fit(
        "[bold green]Installation Complete![/bold green]\n\n"
        f"Install path: {install_path}\n"
        f"Run: [cyan]jarvis[/cyan] to start\n"
        f"Web UI: [cyan]http://localhost:8000[/cyan]",
        border_style="green"
    ))


# ==================== UPDATE ====================

@app.command("update")
def update_cmd(
    check_only: bool = typer.Option(False, "--check-only", help="Only check for updates"),
    channel: Optional[str] = typer.Option(None, "--channel", help="Target channel"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Simulate update"),
    rollback: bool = typer.Option(False, "--rollback", help="Rollback last update"),
    list_rollbacks: bool = typer.Option(False, "--list-rollbacks", help="List available rollbacks"),
    silent: bool = typer.Option(False, "--silent", help="Silent mode (for cron)"),
):
    """Atualiza JARVIS para versão mais recente."""
    asyncio.run(_update_async(check_only, channel, dry_run, rollback, list_rollbacks, silent))


async def _update_async(check_only: bool, channel: Optional[str], dry_run: bool, rollback: bool, list_rollbacks: bool, silent: bool):
    paths = get_jarvis_paths()
    
    if list_rollbacks:
        manager = RollbackManager()
        points = manager.list_rollbacks()
        if not points:
            console.print("[yellow]No rollback points available[/yellow]")
            return
        
        table = Table(title="Available Rollbacks")
        table.add_column("#", style="cyan")
        table.add_column("Version", style="green")
        table.add_column("Date", style="yellow")
        table.add_column("Size (MB)", style="cyan")
        
        for i, p in enumerate(points):
            table.add_row(str(i), p.version, p.timestamp.strftime("%Y-%m-%d %H:%M"), f"{p.size_mb:.1f}")
        console.print(table)
        return
    
    if rollback:
        manager = RollbackManager()
        latest = manager.get_latest()
        if not latest:
            console.print("[red]No rollback points available[/red]")
            raise typer.Exit(1)
        
        if not silent:
            confirm = Confirm.ask(f"Rollback to {latest.version} ({latest.timestamp})?")
            if not confirm:
                return
        
        manager = RollbackManager()
        success = await manager.rollback_latest()
        if success:
            console.print("[green]Rollback completed[/green]")
        else:
            console.print("[red]Rollback failed[/red]")
            raise typer.Exit(1)
        return
    
    # Check for updates
    target_channel = Channel(channel) if channel else None
    
    async with UpdateChecker() as checker:
        if not silent:
            with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
                task = progress.add_task("Checking for updates...", total=None)
                release = await checker.check_for_updates(target_channel)
                progress.update(task, completed=True)
        
        if not release:
            if not silent:
                console.print("[green]Already up to date[/green]")
            return
        
        console.print(f"[cyan]Update available:[/cyan] {release.version} ({release.published_at[:10]})")
        console.print(f"[dim]{release.body[:200]}...[/dim]")
        
        if check_only:
            return
        
        # Confirm
        if not silent:
            if not Confirm.ask(f"Apply update to {release.version}?"):
                return
        
        # Download assets
        async with Downloader(paths.updates_pending) as downloader:
            with Progress(
                SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
                BarColumn(), TaskProgressColumn(), console=console
            ) as progress:
                task = progress.add_task("Downloading assets...", total=None)
                assets = await downloader.download_release_assets(release)
                progress.update(task, completed=True)
        
        if not assets:
            console.print("[red]No assets downloaded[/red]")
            raise typer.Exit(1)
        
        # Apply update
        console.print(f"[cyan]Applying update...[/cyan]")
        notifier = UpdateNotifier()
        await notifier.notify_update_started(str(release.version))
        
        result = await apply_update(release, assets, dry_run=dry_run)
        
        if result.success:
            console.print(f"[green]Update to {result.version} successful![/green]")
            await UpdateNotifier().notify_update_success(result.version)
        else:
            console.print(f"[red]Update failed: {result.message}[/red]")
            await UpdateNotifier().notify_update_failed(str(release.version), result.message, result.rolled_back)
            if result.rolled_back:
                console.print("[yellow]Rollback performed automatically[/yellow]")
            raise typer.Exit(1)


# ==================== SERVICE ====================

@app.command("service")
def service_cmd(
    action: str = typer.Argument(..., help="Action: start, stop, restart, status, enable, disable, install, uninstall"),
):
    """Gerencia serviço do sistema."""
    if action == "status":
        status = get_service_status()
        console.print(f"Service status: [cyan]{status}[/cyan]")
    elif action == "start":
        start_service()
        console.print("[green]Service started[/green]")
    elif action == "stop":
        stop_service()
        console.print("[yellow]Service stopped[/yellow]")
    elif action == "restart":
        restart_service()
        console.print("[green]Service restarted[/green]")
    elif action == "install":
        from ..install.service import get_service_config, install_systemd_service, install_nssm_service, install_launchd_service
        import platform
        system = platform.system().lower()
        if system == "linux":
            install_systemd_service(get_service_config())
        elif system == "windows":
            install_nssm_service(get_service_config())
        elif system == "darwin":
            install_launchd_service(get_service_config())
        console.print("[green]Service installed[/green]")
    elif action == "uninstall":
        uninstall_service()
        console.print("[yellow]Service uninstalled[/yellow]")
    elif action == "enable":
        import subprocess, platform
        system = platform.system().lower()
        if system == "linux":
            subprocess.run(["systemctl", "enable", "jarvis"])
        console.print("[green]Service enabled[/green]")
    elif action == "disable":
        import subprocess, platform
        system = platform.system().lower()
        if system == "linux":
            subprocess.run(["systemctl", "disable", "jarvis"])
        console.print("[yellow]Service disabled[/yellow]")
    else:
        console.print(f"[red]Unknown action: {action}[/red]")


# ==================== CONFIG ====================

@app.command("config")
def config_cmd(
    action: str = typer.Argument(..., help="Action: show, set, get, migrate, reset"),
    key: Optional[str] = typer.Argument(None, help="Config key (for set/get)"),
    value: Optional[str] = typer.Argument(None, help="Config value (for set)"),
):
    """Gerencia configuração."""
    paths = get_jarvis_paths()
    config = load_config(paths.config_file)
    
    if action == "show":
        import yaml
        console.print(yaml.dump(config, allow_unicode=True))
    elif action == "get":
        if not key:
            console.print("[red]Key required for get[/red]")
            raise typer.Exit(1)
        from ..core.config import get_config_value
        val = get_config_value(config, key)
        console.print(f"{key} = {val}")
    elif action == "set":
        if not key or value is None:
            console.print("[red]Key and value required for set[/red]")
            raise typer.Exit(1)
        from ..core.config import set_config_value
        set_config_value(config, key, value)
        save_config(config, get_jarvis_paths().config_file)
        console.print(f"[green]Set {key} = {value}[/green]")
    elif action == "migrate":
        ensure_config_migrated(get_jarvis_paths().config_file)
        console.print("[green]Config migrated[/green]")
    elif action == "reset":
        if Confirm.ask("Reset config to defaults?"):
            from ..core.config import DEFAULT_CONFIG
            save_config(DEFAULT_CONFIG, get_jarvis_paths().config_file)
            console.print("[green]Config reset to defaults[/green]")
    else:
        console.print(f"[red]Unknown action: {action}[/red]")


# ==================== STATUS / HEALTH ====================

@app.command("status")
def status_cmd(
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Mostra status do sistema."""
    asyncio.run(_status_async(json_output))


async def _status_async(json_output: bool):
    paths = get_jarvis_paths()
    config = load_config(paths.config_file)
    
    # System info
    system = detect_system()
    
    # Health check
    report = await run_health_check()
    
    # Service status
    service_status = get_service_status()
    
    if json_output:
        import json
        data = {
            "version": get_version_ctx()["version"],
            "channel": get_version_ctx()["channel"],
            "system": {
                "os": system.os,
                "python": system.python_version,
                "gpu": system.has_gpu,
                "cuda": system.cuda_available,
            },
            "health": {
                "overall": report.overall_healthy,
                "checks": {k: {"healthy": v.healthy, "message": v.message} for k, v in report.results.items()},
            },
            "service": service_status,
            "config": {k: v for k, v in config.items() if k != "gemini"},  # Hide API key
        }
        console.print_json(json.dumps(data, indent=2))
    else:
        # Rich table output
        console.print(Panel.fit(f"[bold cyan]JARVIS Status[/bold cyan]\nVersion: {get_version_ctx()['version']} ({get_version_ctx()['channel']})", border_style="cyan"))
        
        table = Table(title="System")
        table.add_column("Property", style="cyan")
        table.add_column("Value", style="green")
        table.add_row("OS", f"{system.os} {system.os_version} ({system.arch})")
        table.add_row("Python", system.python_version)
        table.add_row("GPU", f"{system.gpu_name} ({system.gpu_memory_mb} MB)" if system.has_gpu else "None")
        table.add_row("CUDA", system.cuda_version or "Not available")
        table.add_row("RAM", f"{system.total_memory_mb} MB total, {system.available_memory_mb} MB free")
        table.add_row("Disk", f"{system.disk_free_gb:.1f} GB free")
        console.print(table)
        
        table2 = Table(title="Health Checks")
        table2.add_column("Check", style="cyan")
        table2.add_column("Status", style="green")
        table2.add_column("Message", style="yellow")
        for name, result in report.results.items():
            status = "[green]✓[/green]" if result.healthy else "[red]✗[/red]"
            table2.add_row(name, status, result.message)
        console.print(table2)
        
        console.print(f"\nService: [cyan]{service_status}[/cyan]")


# ==================== HEALTH / DOCTOR ====================

@app.command("doctor")
def doctor_cmd(
    fix: bool = typer.Option(False, "--fix", help="Attempt to fix issues"),
):
    """Diagnóstico completo do sistema."""
    asyncio.run(_doctor_async(fix))


async def _doctor_async(fix: bool):
    console.print(Panel.fit("[bold cyan]JARVIS Doctor[/bold cyan]", border_style="cyan"))
    
    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
        task = progress.add_task("Running diagnostics...", total=None)
        report = await run_health_check()
        progress.update(task, completed=True)
    
    issues = [(k, v) for k, v in report.results.items() if not v.healthy]
    
    if not issues:
        console.print("[green]All checks passed![/green]")
        return
    
    console.print(f"[red]Found {len(issues)} issue(s):[/red]")
    for name, result in issues:
        console.print(f"  [red]✗[/red] {name}: {result.message}")
    
    if fix:
        console.print("[cyan]Attempting fixes...[/cyan]")
        # TODO: Implement auto-fixes
        console.print("[yellow]Auto-fix not yet implemented[/yellow]")


# ==================== LOGS ====================

@app.command("logs")
def logs_cmd(
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow log output"),
    lines: int = typer.Option(50, "--lines", "-n", help="Number of lines"),
    service: bool = typer.Option(False, "--service", help="Show service logs (journalctl)"),
):
    """Mostra logs."""
    paths = get_jarvis_paths()
    
    if service:
        import subprocess
        cmd = ["journalctl", "-u", "jarvis", "-n", str(lines)]
        if follow:
            cmd.insert(-1, "-f")
        subprocess.run(cmd)
    else:
        log_file = paths.log_file
        if log_file.exists():
            import subprocess
            cmd = ["tail", "-n", str(lines)]
            if follow:
                cmd.insert(1, "-f")
            cmd.append(str(log_file))
            subprocess.run(cmd)
        else:
            console.print("[yellow]Log file not found[/yellow]")


# ==================== FRONTEND ====================

@app.command("frontend")
def frontend_cmd(
    action: str = typer.Argument(..., help="Action: build, dev, clean"),
):
    """Gerencia frontend."""
    paths = get_jarvis_paths()
    
    if action == "build":
        console.print("[cyan]Building frontend...[/cyan]")
        if build_frontend(paths.frontend):
            console.print("[green]Build successful[/green]")
        else:
            console.print("[red]Build failed[/red]")
            raise typer.Exit(1)
    elif action == "dev":
        console.print("[cyan]Starting dev server...[/cyan]")
        subprocess.run(["npm", "run", "dev"], cwd=paths.frontend)
    elif action == "clean":
        import shutil
        dist = paths.frontend / "dist"
        if dist.exists():
            shutil.rmtree(dist)
        console.print("[green]Frontend cleaned[/green]")
    else:
        console.print(f"[red]Unknown action: {action}[/red]")


# ==================== UNINSTALL ====================

@app.command("uninstall")
def uninstall_cmd(
    keep_data: bool = typer.Option(True, "--keep-data", help="Keep config and data"),
    force: bool = typer.Option(False, "--force", help="Skip confirmation"),
):
    """Desinstala JARVIS."""
    paths = get_jarvis_paths()
    
    if not force:
        if not Confirm.ask(f"Uninstall JARVIS from {paths.base}?"):
            return
    
    # Stop service
    try:
        stop_service()
        uninstall_service()
    except Exception:
        pass
    
    # Remove shortcuts
    remove_shortcuts("JARVIS")
    
    # Remove files
    if not keep_data:
        import shutil
        shutil.rmtree(paths.base, ignore_errors=True)
        console.print("[green]Uninstalled completely[/green]")
    else:
        # Remove only binaries and builds
        import shutil
        if paths.venv.exists():
            shutil.rmtree(paths.venv)
        if paths.frontend_dist.exists():
            shutil.rmtree(paths.frontend_dist)
        console.print("[green]Uninstalled (data preserved)[/green]")


# ==================== BENCHMARK ====================

@app.command("benchmark")
def benchmark_cmd(
    iterations: int = typer.Option(10, "--iterations", "-n", help="Number of iterations"),
):
    """Benchmark de latência."""
    asyncio.run(_benchmark_async(iterations))


async def _benchmark_async(iterations: int):
    console.print(f"[cyan]Running benchmark ({iterations} iterations)...[/cyan]")
    
    from ..core.brain import get_current_version
    from ..core.config import load_config
    from ..paths import get_jarvis_paths
    
    paths = get_jarvis_paths()
    config = load_config(paths.config_file)
    
    # Initialize app
    from ..bootstrap import create_application
    app = create_application()
    app.initialize_brains()
    
    from ..core.session import SessionManager
    session_mgr = SessionManager()
    
    times = []
    for i in range(iterations):
        start = asyncio.get_event_loop().time()
        # Simulate voice loop: text input -> brain -> response
        context = session_mgr.get_or_create("benchmark", "test", f"bench_{i}")
        async for _ in app.brain_factory.get("fast").process(session_mgr.get_or_create("benchmark", "test", f"bench_{i}"), text_input="que horas são"):
            pass
        elapsed = (asyncio.get_event_loop().time() - start) * 1000
        times.append(elapsed)
        if (i + 1) % 5 == 0:
            console.print(f"  Iteration {i+1}: {elapsed:.1f}ms")
    
    avg = sum(times) / len(times)
    p50 = sorted(times)[len(times)//2]
    p95 = sorted(times)[int(len(times)*0.95)]
    
    console.print(f"\n[bold]Results ({iterations} iterations):[/bold]")
    console.print(f"  Average: {avg:.1f}ms")
    console.print(f"  Median (p50): {p50:.1f}ms")
    console.print(f"  p95: {p95:.1f}ms")
    console.print(f"  Min: {min(times):.1f}ms")
    console.print(f"  Max: {max(times):.1f}ms")


# ==================== MAIN ====================

if __name__ == "__main__":
    app()