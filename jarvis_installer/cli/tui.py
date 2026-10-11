from __future__ import annotations

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Header, Footer, Static, DataTable, Log, Button, Input, Select, Tabs, TabPane
from textual.binding import Binding
from textual.reactive import reactive
from textual import events
import asyncio
from pathlib import Path

from ..paths import get_jarvis_paths
from ..core.system import detect_system
from ..core.config import load_config
from ..core.health import run_health_check
from ..core.version import get_current_version, get_current_channel, Channel
from ..update.checker import UpdateChecker
from ..update.downloader import Downloader
from ..update.applier import apply_update
from ..update.rollback import RollbackManager
from ..install.service import get_service_status, start_service, stop_service, restart_service


class JARVISTUI(App):
    """Terminal UI para JARVIS Installer/Updater."""
    
    CSS = """
    Screen {
        background: $surface;
    }
    .sidebar {
        width: 30;
        background: $panel;
        border-right: solid $primary;
    }
    .content {
        width: 1fr;
        background: $surface;
    }
    .status-bar {
        height: 3;
        background: $primary;
        color: $text;
        padding: 0 1;
    }
    DataTable {
        height: 1fr;
    }
    .panel {
        border: solid $primary;
        padding: 1;
        margin: 1;
    }
    Button {
        margin: 1;
    }
    """
    
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("u", "update", "Check Updates"),
        Binding("s", "service", "Service Menu"),
        Binding("h", "health", "Health Check"),
        Binding("l", "logs", "View Logs"),
        Binding("c", "config", "Config Editor"),
    )
    
    current_tab: reactive[str] = reactive("dashboard")
    system_info = None
    health_report = None
    
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal():
            with Vertical(classes="sidebar"):
                yield Static("[bold cyan]JARVIS[/bold cyan]\n[dim]Installer & Updater[/dim]", classes="panel")
                yield Button("Dashboard", id="btn-dashboard", variant="primary")
                yield Button("Health", id="btn-health")
                yield Button("Updates", id="btn-updates")
                yield Button("Services", id="btn-services")
                yield Button("Config", id="btn-config")
                yield Button("Logs", id="btn-logs")
                yield Button("Benchmark", id="btn-benchmark")
            with Vertical(classes="content"):
                yield Tabs(
                    TabPane("Dashboard", id="tab-dashboard"),
                    TabPane("Health", id="tab-health"),
                    TabPane("Updates", id="tab-updates"),
                    TabPane("Services", id="tab-services"),
                    TabPane("Config", id="tab-config"),
                    TabPane("Logs", id="tab-logs"),
                    TabPane("Benchmark", id="tab-benchmark"),
                )
        yield Footer()
    
    async def on_mount(self) -> None:
        self.system_info = detect_system()
        await self.refresh_all()
    
    async def refresh_all(self) -> None:
        await self.refresh_dashboard()
        await self.refresh_health()
        await self.check_updates()
    
    async def refresh_dashboard(self) -> None:
        tab = self.query_one("#tab-dashboard")
        if self.system_info:
            tab.clear()
            tab.mount(Static(f"""
[bold cyan]JARVIS CyberBot[/bold cyan]
Version: {get_current_version()} ({get_current_channel().value})

[bold]System:[/bold]
  OS: {self.system_info.os} {self.system_info.os_version} ({self.system_info.arch})
  Python: {self.system_info.python_version}
  GPU: {self.system_info.gpu_name or 'None'} ({self.system_info.gpu_memory_mb or 0} MB)
  CUDA: {self.system_info.cuda_version or 'N/A'}
  RAM: {self.system_info.total_memory_mb} MB total, {self.system_info.available_memory_mb} MB free
  Disk: {self.system_info.disk_free_gb:.1f} GB free

[bold]Paths:[/bold]
  Base: {get_jarvis_paths().base}
  Venv: {get_jarvis_paths().venv}
  Config: {get_jarvis_paths().config_file}
"""))
    
    async def refresh_health(self) -> None:
        self.health_report = await run_health_check()
        tab = self.query_one("#tab-health")
        tab.clear()
        
        if self.health_report:
            table = DataTable()
            table.add_columns("Check", "Status", "Message", "Duration (ms)")
            for name, result in self.health_report.results.items():
                status = "✓" if result.healthy else "✗"
                style = "green" if result.healthy else "red"
                table.add_row(name, f"[{style}]{status}[/{style}]", result.message, f"{result.duration_ms:.1f}")
            tab.mount(table)
    
    async def check_updates(self) -> None:
        try:
            async with UpdateChecker() as checker:
                release = await checker.check_for_updates()
                tab = self.query_one("#tab-updates")
                tab.clear()
                
                if release:
                    tab.mount(Static(f"""
[bold green]Update Available![/bold green]
Version: {release.version}
Published: {release.published_at[:10]}
Channel: {release.version.channel.value}

{release.body[:300]}...
"""))
                    # Add update button
                else:
                    tab.mount(Static("[green]Already up to date[/green]"))
        except Exception as e:
            tab = self.query_one("#tab-updates")
            tab.clear()
            tab.mount(Static(f"[red]Error checking updates: {e}[/red]"))
    
    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "btn-dashboard":
            self.current_tab = "dashboard"
        elif button_id == "btn-health":
            self.current_tab = "health"
        elif button_id == "btn-updates":
            self.current_tab = "updates"
        elif button_id == "btn-services":
            self.current_tab = "services"
        elif button_id == "btn-config":
            self.current_tab = "config"
        elif button_id == "btn-logs":
            self.current_tab = "logs"
        elif button_id == "btn-benchmark":
            self.current_tab = "benchmark"
        
        # Switch tab
        tabs = self.query_one(Tabs)
        tabs.active = f"tab-{self.current_tab}"
    
    async def action_refresh(self) -> None:
        await self.refresh_all()
    
    async def action_update(self) -> None:
        await self.check_updates()
    
    async def action_health(self) -> None:
        await self.refresh_health()
    
    async def action_service(self) -> None:
        # Show service status
        status = get_service_status()
        self.notify(f"Service status: {status}")

    async def on_tab_activated(self, event: Tabs.TabActivated) -> None:
        self.current_tab = event.tab.id.replace("tab-", "")
        if self.current_tab == "health":
            await self.refresh_health()
        elif self.current_tab == "updates":
            await self.check_updates()


def run_tui():
    app = JARVISTUI()
    app.run()