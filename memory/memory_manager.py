import json
from datetime import datetime
from threading import Lock
from pathlib import Path
import sys


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR = get_base_dir()
MEMORY_PATH = BASE_DIR / "memory" / "long_term.json"
_lock = Lock()
MAX_VALUE_LENGTH = 380
MEMORY_MAX_CHARS = 2200


def _empty_memory() -> dict:
    return {
        "identity": {},
        "preferences": {},
        "projects": {},
        "relationships": {},
        "wishes": {},
        "notes": {},
    }


def load_memory() -> dict:
    if not MEMORY_PATH.exists():
        return _empty_memory()
    with _lock:
        try:
            data = json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                base = _empty_memory()
                for key in base:
                    if key not in data or not isinstance(data[key], dict):
                        data[key] = {}
                return data
        except Exception as exc:
            print(f"[Memory] load error: {exc}")
    return _empty_memory()


def _all_entries(memory: dict) -> list[tuple]:
    entries = []
    for category, items in memory.items():
        if not isinstance(items, dict):
            continue
        for key, entry in items.items():
            if isinstance(entry, dict) and "value" in entry:
                entries.append((category, key, entry))
    return entries


def _trim_to_limit(memory: dict) -> dict:
    if len(json.dumps(memory, ensure_ascii=False)) <= MEMORY_MAX_CHARS:
        return memory
    entries = _all_entries(memory)
    entries.sort(key=lambda item: item[2].get("updated", "0000-00-00"))
    for category, key, _ in entries:
        if len(json.dumps(memory, ensure_ascii=False)) <= MEMORY_MAX_CHARS:
            break
        del memory[category][key]
    return memory


def save_memory(memory: dict) -> None:
    if not isinstance(memory, dict):
        return
    memory = _trim_to_limit(memory)
    MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        MEMORY_PATH.write_text(
            json.dumps(memory, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


def _truncate_value(value: str) -> str:
    if isinstance(value, str) and len(value) > MAX_VALUE_LENGTH:
        return value[:MAX_VALUE_LENGTH].rstrip() + "…"
    return value


def _recursive_update(target: dict, updates: dict) -> bool:
    changed = False
    for key, value in updates.items():
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        if isinstance(value, dict) and "value" not in value:
            if key not in target or not isinstance(target[key], dict):
                target[key] = {}
                changed = True
            if _recursive_update(target[key], value):
                changed = True
            continue
        raw = value.get("value") if isinstance(value, dict) else value
        new_value = _truncate_value(str(raw))
        entry = {"value": new_value, "updated": datetime.now().strftime("%Y-%m-%d")}
        if not isinstance(target.get(key), dict) or target[key].get("value") != new_value:
            target[key] = entry
            changed = True
    return changed


def update_memory(memory_update: dict) -> dict:
    if not isinstance(memory_update, dict) or not memory_update:
        return load_memory()
    memory = load_memory()
    if _recursive_update(memory, memory_update):
        save_memory(memory)
    return memory


def format_memory_for_prompt(memory: dict | None) -> str:
    if not memory:
        return ""
    lines = []
    for category, items in memory.items():
        if not isinstance(items, dict) or not items:
            continue
        lines.append(f"{category.title()}:")
        for key, entry in list(items.items())[:15]:
            value = entry.get("value") if isinstance(entry, dict) else entry
            if value:
                lines.append(f"  - {str(key).replace('_', ' ').title()}: {value}")
    if not lines:
        return ""
    result = "[WHAT YOU KNOW ABOUT THIS PERSON — use naturally, never recite like a list]\n" + "\n".join(lines)
    return result[:1997] + "…\n" if len(result) > 2000 else result + "\n"


def remember(key: str, value: str, category: str = "notes") -> str:
    if category not in {"identity", "preferences", "projects", "relationships", "wishes", "notes"}:
        category = "notes"
    update_memory({category: {key: {"value": value}}})
    return f"Remembered: {category}/{key} = {value}"


def forget(key: str, category: str = "notes") -> str:
    memory = load_memory()
    values = memory.get(category, {})
    if key in values:
        del values[key]
        memory[category] = values
        save_memory(memory)
        return f"Forgotten: {category}/{key}"
    return f"Not found: {category}/{key}"


forget_memory = forget
