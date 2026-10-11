from __future__ import annotations

import platform
import os
import subprocess
import json
from pathlib import Path
from typing import Optional


def create_desktop_shortcut(app_name: str, executable: Path, icon: Path = None, 
                           arguments: str = "", working_dir: Path = None) -> bool:
    """Cria atalho na área de trabalho."""
    system = platform.system().lower()
    
    if system == "windows":
        return _create_windows_shortcut(app_name, executable, icon, arguments, working_dir)
    elif system == "darwin":
        return _create_macos_shortcut(app_name, executable, icon, arguments, working_dir)
    else:
        return _create_linux_shortcut(app_name, executable, icon, arguments, working_dir)


def _create_windows_shortcut(app_name: str, executable: Path, icon: Path = None,
                            arguments: str = "", working_dir: Path = None) -> bool:
    """Cria atalho .lnk no Windows via PowerShell."""
    try:
        desktop = Path(os.environ.get("USERPROFILE", "")) / "Desktop"
        shortcut_path = desktop / f"{app_name}.lnk"
        
        ps_script = f"""
        $WshShell = New-Object -comObject WScript.Shell
        $Shortcut = $WshShell.CreateShortcut("{shortcut_path}")
        $Shortcut.TargetPath = "{executable}"
        $Shortcut.Arguments = "{arguments}"
        $Shortcut.WorkingDirectory = "{working_dir or executable.parent}"
        if ("{icon}" -ne "") {{ $Shortcut.IconLocation = "{icon}" }}
        $Shortcut.Save()
        """
        
        result = subprocess.run(["powershell", "-Command", ps_script], 
                              capture_output=True, text=True, timeout=30)
        return result.returncode == 0
    except Exception as e:
        print(f"[Shortcut] Windows error: {e}")
        return False


def _create_macos_shortcut(app_name: str, executable: Path, icon: Path = None,
                          arguments: str = "", working_dir: Path = None) -> bool:
    """Cria alias no macOS (via AppleScript)."""
    try:
        desktop = Path.home() / "Desktop"
        alias_path = desktop / f"{app_name}.app"
        
        # Create a simple app bundle
        app_bundle = Path.home() / "Applications" / f"{app_name}.app"
        app_bundle.mkdir(parents=True, exist_ok=True)
        
        contents = app_bundle / "Contents"
        contents.mkdir(exist_ok=True)
        macos = contents / "MacOS"
        macos.mkdir(exist_ok=True)
        
        # Info.plist
        plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>launcher</string>
    <key>CFBundleIdentifier</key>
    <string>com.jarvis.{app_name.lower()}</string>
    <key>CFBundleName</key>
    <string>{app_name}</string>
    <key>CFBundleVersion</key>
    <string>1.0</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0</string>
</dict>
</plist>"""
        (contents / "Info.plist").write_text(plist)
        
        # Launcher script
        launcher = f"""#!/bin/bash
cd "{working_dir or executable.parent}"
exec "{executable}" {arguments}
"""
        launcher_path = macos / "launcher"
        launcher_path.write_text(launcher)
        launcher_path.chmod(0o755)
        
        # Icon
        if icon and icon.exists():
            import shutil
            shutil.copy2(icon, contents / "Resources" / "icon.icns")
        
        # Create alias on Desktop
        subprocess.run(["ln", "-sf", str(app_bundle), str(alias_path)], check=True)
        return True
    except Exception as e:
        print(f"[Shortcut] macOS error: {e}")
        return False


def _create_linux_shortcut(app_name: str, executable: Path, icon: Path = None,
                          arguments: str = "", working_dir: Path = None) -> bool:
    """Cria .desktop file no Linux."""
    try:
        desktop_dir = Path.home() / ".local" / "share" / "applications"
        desktop_dir.mkdir(parents=True, exist_ok=True)
        
        desktop_file = desktop_dir / f"{app_name.lower().replace(' ', '-')}.desktop"
        
        icon_path = str(icon) if icon and icon.exists() else app_name.lower()
        
        content = f"""[Desktop Entry]
Version=1.0
Type=Application
Name={app_name}
Comment=JARVIS CyberBot Assistant
Exec={executable} {arguments}
Path={working_dir or executable.parent}
Icon={icon_path}
Terminal=false
Categories=Utility;Office;
StartupNotify=true
"""
        desktop_file.write_text(content)
        
        # Also create desktop shortcut
        user_desktop = Path(os.environ.get("XDG_DESKTOP_DIR", Path.home() / "Desktop"))
        if user_desktop.exists():
            user_shortcut = user_desktop / f"{app_name}.desktop"
            user_shortcut.write_text(content)
            user_shortcut.chmod(0o755)
        
        return True
    except Exception as e:
        print(f"[Shortcut] Linux error: {e}")
        return False


def create_start_menu_entry(app_name: str, executable: Path, icon: Path = None,
                           arguments: str = "") -> bool:
    """Cria entrada no menu Iniciar (Windows) ou menu de aplicações (Linux)."""
    system = platform.system().lower()
    
    if system == "windows":
        # Windows: cria atalho em Start Menu
        try:
            start_menu = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
            start_menu.mkdir(parents=True, exist_ok=True)
            shortcut_path = start_menu / f"{app_name}.lnk"
            
            ps_script = f"""
            $WshShell = New-Object -comObject WScript.Shell
            $Shortcut = $WshShell.CreateShortcut("{shortcut_path}")
            $Shortcut.TargetPath = "{executable}"
            $Shortcut.Arguments = "{arguments}"
            $Shortcut.WorkingDirectory = "{executable.parent}"
            $Shortcut.Save()
            """
            result = subprocess.run(["powershell", "-Command", ps_script], 
                                  capture_output=True, text=True, timeout=30)
            return result.returncode == 0
        except Exception:
            return False
    
    elif system == "linux":
        # Linux: .desktop file já cobre isso
        return True
    
    return False


def remove_shortcuts(app_name: str) -> bool:
    """Remove todos os atalhos criados."""
    system = platform.system().lower()
    
    try:
        if system == "windows":
            # Desktop
            desktop = Path(os.environ.get("USERPROFILE", "")) / "Desktop"
            for shortcut in desktop.glob(f"{app_name}*.lnk"):
                shortcut.unlink(missing_ok=True)
            # Start Menu
            start_menu = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
            for shortcut in start_menu.glob(f"{app_name}*.lnk"):
                shortcut.unlink(missing_ok=True)
        
        elif system == "darwin":
            # Desktop alias
            desktop = Path.home() / "Desktop"
            for alias in desktop.glob(f"{app_name}*"):
                alias.unlink(missing_ok=True)
            # Applications
            apps = Path.home() / "Applications"
            for app in apps.glob(f"{app_name}*"):
                shutil.rmtree(app, ignore_errors=True)
        
        elif system == "linux":
            # .desktop files
            apps_dir = Path.home() / ".local" / "share" / "applications"
            for desktop_file in apps_dir.glob(f"*{app_name.lower()}*.desktop"):
                desktop_file.unlink(missing_ok=True)
            # Desktop
            desktop = Path(os.environ.get("XDG_DESKTOP_DIR", Path.home() / "Desktop"))
            for shortcut in desktop.glob(f"*{app_name}*.desktop"):
                shortcut.unlink(missing_ok=True)
        
        return True
    except Exception as e:
        print(f"[Shortcut] Remove error: {e}")
        return False