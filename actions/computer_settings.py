#computer_settings.py
import json
import re
import os
import sys
import time
import subprocess
import platform
import unicodedata
from pathlib import Path

try:
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE    = 0.05
    _PYAUTOGUI = True
except ImportError:
    _PYAUTOGUI = False

try:
    import pyperclip
    _PYPERCLIP = True
except ImportError:
    _PYPERCLIP = False

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"

if _OS == "Windows":
    _WIN_HIDE: dict = {"creationflags": subprocess.CREATE_NO_WINDOW}
else:
    _WIN_HIDE: dict = {}


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

def _get_api_key() -> str:
    path = _get_base_dir() / "config" / "api_keys.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]

def _get_macos_wifi_interface() -> str:
    try:
        result = subprocess.run(
            ["networksetup", "-listallhardwareports"],
            capture_output=True, text=True, timeout=5
        )
        lines = result.stdout.splitlines()
        for i, line in enumerate(lines):
            if "Wi-Fi" in line or "AirPort" in line:
                for j in range(i, min(i + 4, len(lines))):
                    if lines[j].startswith("Device:"):
                        return lines[j].split(":", 1)[1].strip()
    except Exception:
        pass
    return "en0" 

def _clamp_volume(value) -> int:
    try:
        return max(0, min(100, int(round(float(value)))))
    except Exception:
        return 50


def _parse_volume_percent(*values) -> int | None:
    """Extrai porcentagem de textos como '10%', 'volume para 90' ou valor='10'."""
    text = " ".join(str(v or "") for v in values)
    if not text.strip():
        return None
    match = re.search(r"(?:^|\D)(100|[1-9]?\d)\s*%?", text)
    if not match:
        return None
    return _clamp_volume(match.group(1))


def _windows_volume_endpoint():
    """Retorna endpoint de volume do Windows via pycaw, com COM inicializado."""
    if _OS != "Windows":
        return None, None
    try:
        from ctypes import POINTER, cast
        import comtypes
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

        initialized = False
        try:
            comtypes.CoInitialize()
            initialized = True
        except Exception:
            pass

        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        endpoint = cast(interface, POINTER(IAudioEndpointVolume))
        return endpoint, initialized
    except Exception as e:
        print(f"[Settings] pycaw unavailable: {e}")
        return None, None


def _windows_get_volume_percent() -> int | None:
    endpoint, initialized = _windows_volume_endpoint()
    if not endpoint:
        return None
    try:
        return _clamp_volume(endpoint.GetMasterVolumeLevelScalar() * 100)
    except Exception as e:
        print(f"[Settings] get volume failed: {e}")
        return None
    finally:
        if initialized:
            try:
                import comtypes
                comtypes.CoUninitialize()
            except Exception:
                pass


def _windows_set_volume_percent(value: int) -> bool:
    endpoint, initialized = _windows_volume_endpoint()
    if not endpoint:
        return False
    try:
        scalar = _clamp_volume(value) / 100.0
        endpoint.SetMute(0, None)
        endpoint.SetMasterVolumeLevelScalar(scalar, None)
        return True
    except Exception as e:
        print(f"[Settings] set volume failed: {e}")
        return False
    finally:
        if initialized:
            try:
                import comtypes
                comtypes.CoUninitialize()
            except Exception:
                pass


def _windows_toggle_mute() -> bool:
    endpoint, initialized = _windows_volume_endpoint()
    if not endpoint:
        return False
    try:
        muted = bool(endpoint.GetMute())
        endpoint.SetMute(0 if muted else 1, None)
        return True
    except Exception as e:
        print(f"[Settings] mute failed: {e}")
        return False
    finally:
        if initialized:
            try:
                import comtypes
                comtypes.CoUninitialize()
            except Exception:
                pass


def _press_windows_volume_key(vk_code: int, times: int = 1) -> bool:
    try:
        import ctypes
        user32 = ctypes.windll.user32
        for _ in range(max(1, int(times))):
            user32.keybd_event(vk_code, 0, 0, 0)
            user32.keybd_event(vk_code, 0, 2, 0)
            time.sleep(0.025)
        return True
    except Exception as e:
        print(f"[Settings] Windows volume key fallback failed: {e}")
        if _PYAUTOGUI:
            key = {0xAF: "volumeup", 0xAE: "volumedown", 0xAD: "volumemute"}.get(vk_code)
            if key:
                for _ in range(max(1, int(times))):
                    pyautogui.press(key)
                return True
        return False


def volume_up(step: int = 10):
    step = max(1, min(50, int(step or 10)))
    if _OS == "Windows":
        current = _windows_get_volume_percent()
        if current is not None and _windows_set_volume_percent(current + step):
            return
        _press_windows_volume_key(0xAF, max(1, round(step / 2)))
    elif _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            f"set volume output volume (output volume of (get volume settings) + {step})"],
            capture_output=True)
    else:
        subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"+{step}%"],
            capture_output=True)


def volume_down(step: int = 10):
    step = max(1, min(50, int(step or 10)))
    if _OS == "Windows":
        current = _windows_get_volume_percent()
        if current is not None and _windows_set_volume_percent(current - step):
            return
        _press_windows_volume_key(0xAE, max(1, round(step / 2)))
    elif _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            f"set volume output volume (output volume of (get volume settings) - {step})"],
            capture_output=True)
    else:
        subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"-{step}%"],
            capture_output=True)


def volume_mute():
    if _OS == "Windows":
        if not _windows_toggle_mute():
            _press_windows_volume_key(0xAD, 1)
    elif _OS == "Darwin":
        subprocess.run(["osascript", "-e", "set volume with output muted"],
            capture_output=True)
    else:
        subprocess.run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"],
            capture_output=True)


def volume_set(value: int):
    value = _clamp_volume(value)
    if _OS == "Windows":
        if _windows_set_volume_percent(value):
            return
        # Fallback sem pycaw: baixa até 0 e sobe aproximadamente até o valor pedido.
        _press_windows_volume_key(0xAE, 60)
        if value > 0:
            _press_windows_volume_key(0xAF, max(1, round(value / 2)))
        return
    elif _OS == "Darwin":
        subprocess.run(["osascript", "-e", f"set volume output volume {value}"],
            capture_output=True)
        return
    else:
        subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{value}%"],
            capture_output=True)
        return

def brightness_up():
    if _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            'tell application "System Events" to key code 144'],
            capture_output=True)
    elif _OS == "Linux":
        if subprocess.run(["which", "brightnessctl"],
                capture_output=True).returncode == 0:
            subprocess.run(["brightnessctl", "set", "+10%"], capture_output=True)
        else:
            subprocess.run(
                'xrandr --output $(xrandr | grep " connected" | head -1 | cut -d " " -f1)'
                ' --brightness $(python3 -c "import subprocess; '
                'b=float(subprocess.check_output([\"xrandr\",\"--verbose\"]).decode()'
                '.split(\"Brightness:\")[1].split()[0]); print(min(1.0,b+0.1))")',
                shell=True, capture_output=True
            )
    else:
        try:
            subprocess.run(
                ["powershell", "-Command",
                 "(Get-WmiObject -Namespace root/wmi -Class WmiMonitorBrightnessMethods)"
                 ".WmiSetBrightness(1, [math]::Min(100, "
                 "(Get-WmiObject -Namespace root/wmi -Class WmiMonitorBrightness).CurrentBrightness + 10))"],
                capture_output=True, timeout=5, **_WIN_HIDE
            )
        except Exception as e:
            print(f"[Settings] Brightness up failed on Windows: {e}")

def brightness_down():
    if _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            'tell application "System Events" to key code 145'],
            capture_output=True)
    elif _OS == "Linux":
        if subprocess.run(["which", "brightnessctl"],
                capture_output=True).returncode == 0:
            subprocess.run(["brightnessctl", "set", "10%-"], capture_output=True)
        else:
            subprocess.run(
                'xrandr --output $(xrandr | grep " connected" | head -1 | cut -d " " -f1)'
                ' --brightness $(python3 -c "import subprocess; '
                'b=float(subprocess.check_output([\"xrandr\",\"--verbose\"]).decode()'
                '.split(\"Brightness:\")[1].split()[0]); print(max(0.1,b-0.1))")',
                shell=True, capture_output=True
            )
    else:
        try:
            subprocess.run(
                ["powershell", "-Command",
                 "(Get-WmiObject -Namespace root/wmi -Class WmiMonitorBrightnessMethods)"
                 ".WmiSetBrightness(1, [math]::Max(0, "
                 "(Get-WmiObject -Namespace root/wmi -Class WmiMonitorBrightness).CurrentBrightness - 10))"],
                capture_output=True, timeout=5, **_WIN_HIDE
            )
        except Exception as e:
            print(f"[Settings] Brightness down failed on Windows: {e}")


def _norm_close_text(text: str) -> str:
    """Normalize app/window names for robust matching on Portuguese Windows titles."""
    text = str(text or "").strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


_CLOSE_TARGETS: dict[str, dict[str, list[str]]] = {
    "bloco de notas": {"processes": ["notepad"], "titles": ["bloco de notas", "notepad"]},
    "notepad": {"processes": ["notepad"], "titles": ["bloco de notas", "notepad"]},
    "calculadora": {"processes": ["calculatorapp", "calc"], "titles": ["calculadora", "calculator"]},
    "calculator": {"processes": ["calculatorapp", "calc"], "titles": ["calculadora", "calculator"]},
    "paint": {"processes": ["mspaint"], "titles": ["paint"]},
    "chrome": {"processes": ["chrome"], "titles": ["google chrome", "chrome"]},
    "google chrome": {"processes": ["chrome"], "titles": ["google chrome", "chrome"]},
    "edge": {"processes": ["msedge"], "titles": ["microsoft edge", "edge"]},
    "firefox": {"processes": ["firefox"], "titles": ["firefox"]},
    "brave": {"processes": ["brave"], "titles": ["brave"]},
    "opera": {"processes": ["opera"], "titles": ["opera"]},
    "discord": {"processes": ["discord"], "titles": ["discord"]},
    "steam": {"processes": ["steam", "steamwebhelper"], "titles": ["steam"]},
    "whatsapp": {"processes": ["whatsapp"], "titles": ["whatsapp"]},
    "telegram": {"processes": ["telegram"], "titles": ["telegram"]},
    "spotify": {"processes": ["spotify"], "titles": ["spotify"]},
    "vscode": {"processes": ["code"], "titles": ["visual studio code", "vscode"]},
    "visual studio code": {"processes": ["code"], "titles": ["visual studio code", "vscode"]},
    "word": {"processes": ["winword"], "titles": ["word"]},
    "excel": {"processes": ["excel"], "titles": ["excel"]},
    "powerpoint": {"processes": ["powerpnt"], "titles": ["powerpoint"]},
    "teams": {"processes": ["msteams", "ms-teams", "teams"], "titles": ["teams"]},
    "zoom": {"processes": ["zoom"], "titles": ["zoom"]},
    "vlc": {"processes": ["vlc"], "titles": ["vlc"]},
    "obs": {"processes": ["obs64", "obs32", "obs"], "titles": ["obs"]},
    "obs studio": {"processes": ["obs64", "obs32", "obs"], "titles": ["obs"]},
}


def _resolve_close_target(target: str) -> tuple[list[str], list[str], str]:
    raw = str(target or "").strip()
    key = _norm_close_text(raw)
    if not key:
        return [], [], raw

    for alias, data in _CLOSE_TARGETS.items():
        a = _norm_close_text(alias)
        if key == a or key in a or a in key:
            processes = [_norm_close_text(x).replace(" ", "") for x in data.get("processes", [])]
            titles = [_norm_close_text(x) for x in data.get("titles", [])]
            titles.append(key)
            return sorted(set(processes)), sorted(set(titles)), raw

    compact = key.replace(" ", "")
    candidates = {compact}
    if compact.endswith("exe"):
        candidates.add(compact[:-3].rstrip("."))
    first = key.split(" ", 1)[0]
    if first:
        candidates.add(first)
    return sorted(candidates), [key], raw


def _process_name_matches(proc_name: str, candidates: list[str]) -> bool:
    name = _norm_close_text(proc_name or "").replace(" ", "")
    if name.endswith("exe"):
        name = name[:-3]
    return bool(name and any(name == c or name.startswith(c) or c.startswith(name) for c in candidates))


def _find_processes_by_candidates(candidates: list[str]):
    if not candidates:
        return []
    try:
        import psutil
    except Exception:
        return []
    found = []
    current_pid = None
    try:
        current_pid = os.getpid()
    except Exception:
        pass
    try:
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                if current_pid and proc.info.get("pid") == current_pid:
                    continue
                if _process_name_matches(proc.info.get("name", ""), candidates):
                    found.append(proc)
            except Exception:
                continue
    except Exception:
        return []
    return found


def _close_windows_by_title(title_needles: list[str], target_label: str) -> tuple[int, int]:
    """Try graceful close on visible windows. Returns (attempted, still_visible)."""
    if not title_needles:
        return 0, 0
    try:
        import pygetwindow as gw
    except Exception:
        return 0, 0

    def matches(title: str) -> bool:
        t = _norm_close_text(title)
        return bool(t and any(n and n in t for n in title_needles))

    attempted = 0
    matched_titles = []
    try:
        windows = gw.getAllWindows()
    except Exception:
        return 0, 0

    for win in windows:
        title = getattr(win, "title", "") or ""
        if not matches(title):
            continue
        # Do not try to close our own terminal/dashboard by broad accidental match.
        if "mark xlviii" in _norm_close_text(title) and "mark xlviii" not in title_needles:
            continue
        attempted += 1
        matched_titles.append(title)
        try:
            if getattr(win, "isMinimized", False):
                try:
                    win.restore()
                except Exception:
                    pass
            try:
                win.activate()
                time.sleep(0.12)
            except Exception:
                pass
            try:
                win.close()
            except Exception:
                if _PYAUTOGUI:
                    pyautogui.hotkey("alt", "f4")
        except Exception as e:
            print(f"[Settings] Could not close window {title!r}: {e}")

    if attempted:
        time.sleep(0.9)

    still_visible = 0
    try:
        for win in gw.getAllWindows():
            title = getattr(win, "title", "") or ""
            if matches(title):
                still_visible += 1
    except Exception:
        pass
    if attempted:
        print(f"[Settings] Close target {target_label!r}: window attempts={attempted}, remaining={still_visible}, titles={matched_titles[:3]}")
    return attempted, still_visible


def _terminate_processes(processes, timeout: float = 2.0) -> tuple[int, int]:
    """Terminate matching processes only after window-close fallback needs it."""
    if not processes:
        return 0, 0
    try:
        import psutil
    except Exception:
        return 0, 0
    alive = []
    for proc in processes:
        try:
            if proc.is_running():
                proc.terminate()
                alive.append(proc)
        except Exception:
            continue
    try:
        gone, alive = psutil.wait_procs(alive, timeout=timeout)
    except Exception:
        gone, alive = [], alive
    killed = 0
    for proc in alive:
        try:
            proc.kill()
            killed += 1
        except Exception:
            pass
    return len(gone) + killed, len(alive) - killed


def close_named_app(target: str, force: bool = False) -> str:
    """Close a specific app/window by display name, app alias, process name, or window title."""
    target = str(target or "").strip()
    if not target:
        close_app()
        return "Closed the active app/window."

    process_candidates, title_needles, label = _resolve_close_target(target)
    if not process_candidates and not title_needles:
        return f"I couldn't identify what to close from: {target}"

    window_attempts, still_visible = _close_windows_by_title(title_needles, label)
    processes = _find_processes_by_candidates(process_candidates)

    if window_attempts and still_visible == 0:
        return f"Closed {label}."

    if not window_attempts and not processes:
        return f"{label} does not appear to be open."

    # If the user explicitly asks to force/encerrar/matar, or if no visible window exists but the process is running,
    # end the process. This handles apps that stay in background/tray after closing the window.
    should_force = force or window_attempts == 0
    if should_force and processes:
        closed, remaining = _terminate_processes(processes)
        if closed and remaining == 0:
            return f"Closed {label}."
        if closed:
            return f"Closed part of {label}, but {remaining} process(es) may still be running."

    if window_attempts and still_visible:
        return f"I tried to close {label}, but a window is still open. It may be asking to save or confirm."

    return f"I sent the close command to {label}."

def close_app():
    if _OS == "Darwin": pyautogui.hotkey("command", "q")
    else:               pyautogui.hotkey("alt", "f4")

def close_window():
    if _OS == "Darwin": pyautogui.hotkey("command", "w")
    else:               pyautogui.hotkey("ctrl", "w")

def full_screen():
    if _OS == "Darwin": pyautogui.hotkey("ctrl", "command", "f")
    else:               pyautogui.press("f11")

def minimize_window():
    if _OS == "Darwin": pyautogui.hotkey("command", "m")
    else:               pyautogui.hotkey("win", "down")

def maximize_window():
    if _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            'tell application "System Events" to keystroke "f" '
            'using {control down, command down}'],
            capture_output=True)
    elif _OS == "Windows":
        pyautogui.hotkey("win", "up")
    else:
        try:
            subprocess.run(["wmctrl", "-r", ":ACTIVE:", "-b", "add,maximized_vert,maximized_horz"],
                capture_output=True)
        except Exception:
            pyautogui.hotkey("super", "up")

def snap_left():
    if _OS == "Windows":
        pyautogui.hotkey("win", "left")
    elif _OS == "Linux":
        try:
            subprocess.run(["wmctrl", "-r", ":ACTIVE:", "-e", "0,0,0,960,1080"],
                capture_output=True)
        except Exception:
            pass

def snap_right():
    if _OS == "Windows":
        pyautogui.hotkey("win", "right")
    elif _OS == "Linux":
        try:
            subprocess.run(["wmctrl", "-r", ":ACTIVE:", "-e", "0,960,0,960,1080"],
                capture_output=True)
        except Exception:
            pass

def switch_window():
    if _OS == "Darwin": pyautogui.hotkey("command", "tab")
    else:               pyautogui.hotkey("alt", "tab")

def show_desktop():
    if _OS == "Darwin":   pyautogui.hotkey("fn", "f11")
    elif _OS == "Windows": pyautogui.hotkey("win", "d")
    else:                  pyautogui.hotkey("super", "d")

def open_task_manager():
    if _OS == "Windows":
        pyautogui.hotkey("ctrl", "shift", "esc")
    elif _OS == "Darwin":
        subprocess.Popen(["open", "-a", "Activity Monitor"])
    else:
        for cmd in [["gnome-system-monitor"], ["xfce4-taskmanager"], ["htop"]]:
            if subprocess.run(["which", cmd[0]], capture_output=True).returncode == 0:
                subprocess.Popen(cmd)
                break


def focus_search():
    if _OS == "Darwin": pyautogui.hotkey("command", "l")
    else:               pyautogui.hotkey("ctrl", "l")

def pause_video():      pyautogui.press("space")

def refresh_page():
    if _OS == "Darwin": pyautogui.hotkey("command", "r")
    else:               pyautogui.press("f5")

def close_tab():
    if _OS == "Darwin": pyautogui.hotkey("command", "w")
    else:               pyautogui.hotkey("ctrl", "w")

def new_tab():
    if _OS == "Darwin": pyautogui.hotkey("command", "t")
    else:               pyautogui.hotkey("ctrl", "t")

def next_tab():
    if _OS == "Darwin": pyautogui.hotkey("command", "shift", "bracketright")
    else:               pyautogui.hotkey("ctrl", "tab")

def prev_tab():
    if _OS == "Darwin": pyautogui.hotkey("command", "shift", "bracketleft")
    else:               pyautogui.hotkey("ctrl", "shift", "tab")

def go_back():
    if _OS == "Darwin": pyautogui.hotkey("command", "left")
    else:               pyautogui.hotkey("alt", "left")

def go_forward():
    if _OS == "Darwin": pyautogui.hotkey("command", "right")
    else:               pyautogui.hotkey("alt", "right")

def zoom_in():
    if _OS == "Darwin": pyautogui.hotkey("command", "equal")
    else:               pyautogui.hotkey("ctrl", "equal")

def zoom_out():
    if _OS == "Darwin": pyautogui.hotkey("command", "minus")
    else:               pyautogui.hotkey("ctrl", "minus")

def zoom_reset():
    if _OS == "Darwin": pyautogui.hotkey("command", "0")
    else:               pyautogui.hotkey("ctrl", "0")

def find_on_page():
    if _OS == "Darwin": pyautogui.hotkey("command", "f")
    else:               pyautogui.hotkey("ctrl", "f")

def reload_page_n(n: int):
    for _ in range(max(1, n)):
        refresh_page()
        time.sleep(0.8)


def scroll_up(amount: int = 500):    pyautogui.scroll(amount)
def scroll_down(amount: int = 500):  pyautogui.scroll(-amount)

def scroll_top():
    if _OS == "Darwin": pyautogui.hotkey("command", "up")
    else:               pyautogui.hotkey("ctrl", "home")

def scroll_bottom():
    if _OS == "Darwin": pyautogui.hotkey("command", "down")
    else:               pyautogui.hotkey("ctrl", "end")

def page_up():   pyautogui.press("pageup")
def page_down(): pyautogui.press("pagedown")


def copy():
    if _OS == "Darwin": pyautogui.hotkey("command", "c")
    else:               pyautogui.hotkey("ctrl", "c")

def paste():
    if _OS == "Darwin": pyautogui.hotkey("command", "v")
    else:               pyautogui.hotkey("ctrl", "v")

def cut():
    if _OS == "Darwin": pyautogui.hotkey("command", "x")
    else:               pyautogui.hotkey("ctrl", "x")

def undo():
    if _OS == "Darwin": pyautogui.hotkey("command", "z")
    else:               pyautogui.hotkey("ctrl", "z")

def redo():
    if _OS == "Darwin": pyautogui.hotkey("command", "shift", "z")
    else:               pyautogui.hotkey("ctrl", "y")

def select_all():
    if _OS == "Darwin": pyautogui.hotkey("command", "a")
    else:               pyautogui.hotkey("ctrl", "a")

def save_file():
    if _OS == "Darwin": pyautogui.hotkey("command", "s")
    else:               pyautogui.hotkey("ctrl", "s")

def press_enter():   pyautogui.press("enter")
def press_escape():  pyautogui.press("escape")
def press_key(key: str): pyautogui.press(key)

def type_text(text: str, press_enter_after: bool = False):
    if not text:
        return
    if _PYPERCLIP:
        pyperclip.copy(str(text))
        time.sleep(0.15)
        paste()
    else:
        pyautogui.write(str(text), interval=0.03)
    if press_enter_after:
        time.sleep(0.1)
        pyautogui.press("enter")

def take_screenshot():
    if _OS == "Windows":
        pyautogui.hotkey("win", "shift", "s")
    elif _OS == "Darwin":
        pyautogui.hotkey("command", "shift", "3")
    else:
        for cmd in [["scrot"], ["gnome-screenshot"], ["import", "-window", "root", "screenshot.png"]]:
            if subprocess.run(["which", cmd[0]], capture_output=True).returncode == 0:
                subprocess.Popen(cmd)
                return
        pyautogui.hotkey("ctrl", "print_screen")

def lock_screen():
    if _OS == "Windows":
        pyautogui.hotkey("win", "l")
    elif _OS == "Darwin":
        subprocess.run(["pmset", "displaysleepnow"], capture_output=True)
    else:
        for cmd in [
            ["gnome-screensaver-command", "-l"],
            ["xdg-screensaver", "lock"],
            ["loginctl", "lock-session"],
        ]:
            if subprocess.run(["which", cmd[0]], capture_output=True).returncode == 0:
                subprocess.run(cmd, capture_output=True)
                return

def open_system_settings():
    if _OS == "Windows":
        pyautogui.hotkey("win", "i")
    elif _OS == "Darwin":
        subprocess.Popen(["open", "-a", "System Preferences"])
    else:
        for cmd in [["gnome-control-center"], ["xfce4-settings-manager"], ["kcmshell5"]]:
            if subprocess.run(["which", cmd[0]], capture_output=True).returncode == 0:
                subprocess.Popen(cmd)
                return

def open_file_explorer():
    if _OS == "Windows":
        pyautogui.hotkey("win", "e")
    elif _OS == "Darwin":
        subprocess.Popen(["open", str(Path.home())])
    else:
        for cmd in [["nautilus"], ["thunar"], ["dolphin"], ["nemo"]]:
            if subprocess.run(["which", cmd[0]], capture_output=True).returncode == 0:
                subprocess.Popen(cmd)
                return
        subprocess.Popen(["xdg-open", str(Path.home())])

def sleep_display():
    if _OS == "Windows":
        try:
            import ctypes
            ctypes.windll.user32.SendMessageW(0xFFFF, 0x0112, 0xF170, 2)
        except Exception as e:
            print(f"[Settings] sleep_display failed: {e}")
    elif _OS == "Darwin":
        subprocess.run(["pmset", "displaysleepnow"], capture_output=True)
    else:
        subprocess.run(["xset", "dpms", "force", "off"], capture_output=True)

def open_run():
    if _OS == "Windows":
        pyautogui.hotkey("win", "r")

def dark_mode():
    if _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            'tell app "System Events" to tell appearance preferences '
            'to set dark mode to not dark mode'],
            capture_output=True)
    elif _OS == "Windows":
        try:
            import winreg
            key_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Themes\Personalize"
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_ALL_ACCESS)
            current, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            winreg.SetValueEx(key, "AppsUseLightTheme", 0, winreg.REG_DWORD, 1 - current)
            winreg.SetValueEx(key, "SystemUsesLightTheme", 0, winreg.REG_DWORD, 1 - current)
            winreg.CloseKey(key)
        except Exception as e:
            print(f"[Settings] dark_mode registry failed: {e}")
    else:
        try:
            result = subprocess.run(
                ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
                capture_output=True, text=True
            )
            current = result.stdout.strip()
            new_scheme = "'default'" if "dark" in current else "'prefer-dark'"
            subprocess.run(
                ["gsettings", "set", "org.gnome.desktop.interface", "color-scheme", new_scheme],
                capture_output=True
            )
        except Exception as e:
            print(f"[Settings] dark_mode Linux failed: {e}")

def toggle_wifi():
    if _OS == "Darwin":
        iface = _get_macos_wifi_interface()
        result = subprocess.run(
            ["networksetup", "-getairportpower", iface],
            capture_output=True, text=True
        )
        state = "off" if "On" in result.stdout else "on"
        subprocess.run(["networksetup", "-setairportpower", iface, state],
            capture_output=True)
    elif _OS == "Windows":
        try:
            subprocess.run(
                ["powershell", "-Command",
                 "$adapter = Get-NetAdapter | Where-Object {$_.PhysicalMediaType -eq 'Native 802.11'};"
                 "if ($adapter.Status -eq 'Up') { Disable-NetAdapter -Name $adapter.Name -Confirm:$false }"
                 "else { Enable-NetAdapter -Name $adapter.Name -Confirm:$false }"],
                capture_output=True, timeout=10, **_WIN_HIDE
            )
        except Exception as e:
            print(f"[Settings] toggle_wifi Windows failed: {e}")
    else:
        try:
            result = subprocess.run(["nmcli", "radio", "wifi"], capture_output=True, text=True)
            state  = "off" if "enabled" in result.stdout else "on"
            subprocess.run(["nmcli", "radio", "wifi", state], capture_output=True)
        except Exception as e:
            print(f"[Settings] toggle_wifi Linux failed: {e}")

def restart_computer():
    if _OS == "Windows":
        subprocess.run(["shutdown", "/r", "/t", "10"], capture_output=True, **_WIN_HIDE)
    elif _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            'tell application "System Events" to restart'],
            capture_output=True)
    else:
        subprocess.run(["systemctl", "reboot"], capture_output=True)

def shutdown_computer():
    if _OS == "Windows":
        subprocess.run(["shutdown", "/s", "/t", "10"], capture_output=True)
    elif _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            'tell application "System Events" to shut down'],
            capture_output=True)
    else:
        subprocess.run(["systemctl", "poweroff"], capture_output=True)

ACTION_MAP: dict[str, callable] = {
    "volume_up":           volume_up,
    "increase_volume":     volume_up,
    "aumentar_volume":     volume_up,
    "subir_volume":        volume_up,
    "volume_down":         volume_down,
    "decrease_volume":     volume_down,
    "diminuir_volume":     volume_down,
    "baixar_volume":       volume_down,
    "reduzir_volume":      volume_down,
    "mute":                volume_mute,
    "unmute":              volume_mute,
    "toggle_mute":         volume_mute,
    "brightness_up":       brightness_up,
    "brightness_down":     brightness_down,
    "sleep_display":       sleep_display,
    "screen_off":          sleep_display,
    "pause_video":         pause_video,
    "play_pause":          pause_video,
    "close_app":           close_app,
    "close":               close_app,
    "quit":                close_app,
    "exit_app":            close_app,
    "fechar":              close_app,
    "fechar_app":          close_app,
    "encerrar_app":        close_app,
    "close_window":        close_window,
    "full_screen":         full_screen,
    "fullscreen":          full_screen,
    "minimize":            minimize_window,
    "maximize":            maximize_window,
    "snap_left":           snap_left,
    "snap_right":          snap_right,
    "switch_window":       switch_window,
    "show_desktop":        show_desktop,
    # Os nomes que o cerebro inventa para "minimiza todas as janelas"
    # (01/10/2026: "minimize_all" voltava "Unknown action").
    "minimize_all":        show_desktop,
    "minimize_all_windows": show_desktop,
    "minimizar_tudo":      show_desktop,
    "minimizar_todas":     show_desktop,
    "mostrar_area_de_trabalho": show_desktop,
    "task_manager":        open_task_manager,
    "focus_search":        focus_search,
    "refresh_page":        refresh_page,
    "reload":              refresh_page,
    "close_tab":           close_tab,
    "new_tab":             new_tab,
    "next_tab":            next_tab,
    "prev_tab":            prev_tab,
    "go_back":             go_back,
    "go_forward":          go_forward,
    "zoom_in":             zoom_in,
    "zoom_out":            zoom_out,
    "zoom_reset":          zoom_reset,
    "find_on_page":        find_on_page,
    "scroll_up":           scroll_up,
    "scroll_down":         scroll_down,
    "scroll_top":          scroll_top,
    "scroll_bottom":       scroll_bottom,
    "page_up":             page_up,
    "page_down":           page_down,
    "copy":                copy,
    "paste":               paste,
    "cut":                 cut,
    "undo":                undo,
    "redo":                redo,
    "select_all":          select_all,
    "save":                save_file,
    "enter":               press_enter,
    "escape":              press_escape,
    "screenshot":          take_screenshot,
    "lock_screen":         lock_screen,
    "open_settings":       open_system_settings,
    "file_explorer":       open_file_explorer,
    "open_run":            open_run,
    "dark_mode":           dark_mode,
    "toggle_wifi":         toggle_wifi,
    "restart":             restart_computer,
    "shutdown":            shutdown_computer,
}

_DANGEROUS_ACTIONS = {"restart", "shutdown"}



def _detect_action(description: str) -> dict:

    from core import gemini_socorro          # Gemini, e o Codex se ela cair
    _client = gemini_socorro.cliente()

    available = ", ".join(sorted(ACTION_MAP.keys())) + \
                ", volume_set, set_volume, volume, volume_up, volume_down, type_text, press_key, reload_n"

    prompt = f"""You are an intent detector for a computer control assistant.

The user issued a command (possibly in any language): "{description}"

Available actions: {available}

Return ONLY a valid JSON object:
{{"action": "action_name", "value": null_or_value}}

Rules:
- Pick the single best matching action from the available list.
- For volume_set/set_volume/volume: value is an integer 0-100 when the user says a specific percentage.
- For "increase volume a little" use volume_up. For "decrease/lower volume a little" use volume_down.
- For type_text: value is the exact text to type.
- For press_key: value is the key name (e.g. "f5", "tab", "enter").
- For reload_n: value is an integer (number of times to reload).
- If no clear match, pick the closest action.
- Return ONLY the JSON, no explanation, no markdown."""

    try:
        resp = _client.models.generate_content(model="gemini-3.5-flash-lite", contents=prompt)
        text = re.sub(r"```(?:json)?", "", resp.text).strip().rstrip("`").strip()
        return json.loads(text)
    except Exception as e:
        print(f"[Settings] Intent detection failed: {e}")
        return {"action": description.lower().replace(" ", "_"), "value": None}

def computer_settings(
    parameters: dict = None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params      = parameters or {}
    raw_action  = params.get("action", "").strip()
    description = params.get("description", "").strip()
    value       = params.get("value", None)

    if not raw_action and description:
        detected   = _detect_action(description)
        raw_action = detected.get("action", "")
        if value is None:
            value = detected.get("value")

    action = raw_action.lower().strip().replace(" ", "_").replace("-", "_")

    if not action:
        return "No action could be determined."

    volume_actions = {
        "volume", "set_volume", "volume_set", "volume_level", "ajustar_volume", "definir_volume",
        "setar_volume", "alterar_volume", "volume_up", "increase_volume", "aumentar_volume",
        "subir_volume", "volume_down", "decrease_volume", "diminuir_volume", "baixar_volume",
        "reduzir_volume", "lower_volume", "mute", "unmute", "toggle_mute",
    }
    if not _PYAUTOGUI and action not in volume_actions:
        return "pyautogui is not installed. Run: pip install pyautogui"

    print(f"[Settings] Action: {action}  Value: {value}  OS: {_OS}")
    if player:
        player.write_log(f"[Settings] {action}")

    if action in _DANGEROUS_ACTIONS:
        confirmed = str(params.get("confirmed", "")).lower()
        if confirmed not in ("yes", "true", "1", "confirm"):
            return (
                f"This will {action} the computer. "
                f"Please confirm by calling again with confirmed=yes."
            )

    if action in ("close", "close_app", "quit", "exit_app", "fechar", "fechar_app", "encerrar_app", "terminate_app"):
        target = (
            params.get("app_name")
            or params.get("application")
            or params.get("program")
            or value
            or description
            or ""
        )
        force_words = " ".join(str(x or "") for x in [raw_action, description, value]).lower()
        force = any(word in force_words for word in ("force", "forçar", "forcado", "forçado", "matar", "kill", "encerrar"))
        try:
            return close_named_app(str(target).strip(), force=force)
        except Exception as e:
            print(f"[Settings] Close app failed: {e}")
            return f"Could not close {target or 'the active app'}: {e}"

    if action == "list_running_apps":
        try:
            import psutil
            names = []
            for proc in psutil.process_iter(["name"]):
                name = (proc.info.get("name") or "").strip()
                if name and name not in names:
                    names.append(name)
            names = sorted(names)[:80]
            return "Running apps/processes: " + ", ".join(names)
        except Exception as e:
            return f"Could not list running apps: {e}"

    volume_text = " ".join(str(x or "") for x in [raw_action, description, value]).lower()
    volume_set_actions = {"volume_set", "set_volume", "volume", "volume_level", "ajustar_volume", "definir_volume", "setar_volume", "alterar_volume"}
    volume_up_actions = {"volume_up", "increase_volume", "aumentar_volume", "subir_volume", "raise_volume"}
    volume_down_actions = {"volume_down", "decrease_volume", "diminuir_volume", "baixar_volume", "reduzir_volume", "lower_volume"}

    if action in volume_set_actions:
        try:
            # "+10" E "-10" SAO RELATIVOS (01/10/2026). Pedido "aumenta o volume",
            # o cerebro mandou volume_set com value="+10" — e o numero era lido
            # como ABSOLUTO: o volume ia para 10%. Ele pedia para subir e o
            # JARVIS baixava. Sinal na frente (ou "mais"/"menos") = degrau.
            _rel = re.match(r"^\s*(\+|-|mais|menos)\s*(\d{1,3})?\s*%?\s*$",
                            str(value or "").strip().lower())
            if _rel:
                _passo = _clamp_volume(_rel.group(2) or 10) or 10
                if _rel.group(1) in ("+", "mais"):
                    volume_up(_passo)
                    return f"Volume aumentado em cerca de {_passo}%."
                volume_down(_passo)
                return f"Volume diminuído em cerca de {_passo}%."
            pct = _parse_volume_percent(value, description, raw_action)
            if pct is None:
                if any(word in volume_text for word in ("aument", "subir", "increase", "raise", "up")):
                    volume_up(10)
                    return "Volume aumentado em cerca de 10%."
                if any(word in volume_text for word in ("baix", "dimin", "reduz", "lower", "down", "decrease")):
                    volume_down(10)
                    return "Volume diminuído em cerca de 10%."
                pct = 50
            volume_set(pct)
            return f"Volume ajustado para {pct}%."
        except Exception as e:
            return f"Não consegui ajustar o volume: {e}"

    if action in volume_up_actions:
        try:
            step = _parse_volume_percent(value, description) or 10
            volume_up(step)
            return f"Volume aumentado em cerca de {step}%."
        except Exception as e:
            return f"Não consegui aumentar o volume: {e}"

    if action in volume_down_actions:
        try:
            step = _parse_volume_percent(value, description) or 10
            volume_down(step)
            return f"Volume diminuído em cerca de {step}%."
        except Exception as e:
            return f"Não consegui diminuir o volume: {e}"

    if action in ("type_text", "write_on_screen", "type", "write"):
        text = str(value or params.get("text", "")).strip()
        if not text:
            return "No text provided to type."
        enter_after = str(params.get("press_enter", "false")).lower() in ("true", "1", "yes")
        type_text(text, press_enter_after=enter_after)
        return f"Typed: {text[:80]}"

    if action == "press_key":
        key = str(value or params.get("key", "")).strip()
        if not key:
            return "No key specified."
        press_key(key)
        return f"Pressed: {key}"

    if action in ("reload_n", "refresh_n", "reload_page_n"):
        try:
            reload_page_n(int(value or 1))
            return f"Reloaded {value or 1} time(s)."
        except Exception as e:
            return f"Reload failed: {e}"

    if action == "scroll_up":
        scroll_up(int(value or 500))
        return "Scrolled up."

    if action == "scroll_down":
        scroll_down(int(value or 500))
        return "Scrolled down."

    func = ACTION_MAP.get(action)
    if not func:
        return f"Unknown action: '{raw_action}'."

    try:
        func()
        return f"Done: {action}."
    except Exception as e:
        print(f"[Settings] Action failed ({action}): {e}")
        return f"Action failed ({action}): {e}"
