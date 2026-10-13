from __future__ import annotations

"""Bootstrap do JARVIS - Inicialização e configuração do sistema."""

import os as _os
import sys as _sys
import platform as _platform
import subprocess as _subprocess
import warnings as _warnings

# ── Linux: SEMPRE PELO LANÇADOR DO JARVIS ─────────────────────────────────
def _linux_pelo_lancador() -> None:
    if not _sys.platform.startswith("linux"):
        return
    try:
        app = _os.path.dirname(_os.path.abspath(__file__))
        raiz = _os.path.dirname(app)
        venv = _os.path.join(raiz, "venv")
        lancador = _os.path.join(raiz, "jarvis.sh")
        if not (_os.path.isdir(venv) and _os.path.isfile(lancador)):
            return
        if _os.path.realpath(_sys.prefix) == _os.path.realpath(venv):
            return
        print("[JARVIS] aberto fora do Python dele; abrindo pelo jarvis.sh")
        _os.execv("/bin/bash", ["bash", lancador])
    except Exception:
        pass


# Carrega filtro de warnings antes de bibliotecas pesadas
# NOTE: Não definimos PYTHONWARNINGS=ignore globalmente para permitir UserWarning (ex: SSL verify=False)
# Bibliotecas específicas (numpy, tensorflow) terão seus warnings filtrados individualmente se necessário
_os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
_os.environ.setdefault("GLOG_minloglevel", "3")
try:
    import sitecustomize  # noqa: F401
except Exception:
    pass

# Não aplicamos simplefilter("ignore") global para preservar UserWarning importantes
# (ex: SSL verification disabled). Configuração de warnings é feita no entrypoint (__main__.py)

# ── Saída de texto à prova de emoji ────────────────────────────────────────
def _saida_de_texto_a_prova_de_emoji() -> None:
    import io as _io
    import sys as _sys
    for nome in ("stdout", "stderr"):
        fluxo = getattr(_sys, nome, None)
        if fluxo is None:
            setattr(_sys, nome, _io.StringIO())
            continue
        try:
            fluxo.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            try:
                setattr(_sys, nome, _io.TextIOWrapper(
                    fluxo.buffer, encoding="utf-8", errors="replace", line_buffering=True))
            except Exception:
                setattr(_sys, nome, _io.StringIO())


# ── Nuclear: force CREATE_NO_WINDOW on Windows ─────────────────────────────
def _patch_subprocess_popen() -> None:
    if _platform.system() == "Windows":
        _OrigPopen = _subprocess.Popen

        class _Popen(_OrigPopen):
            def __init__(self, args, **kw):
                kw["creationflags"] = kw.get("creationflags", 0) | _subprocess.CREATE_NO_WINDOW
                kw.pop("startupinfo", None)
                super().__init__(args, **kw)

        _subprocess.Popen = _Popen


# ── os.startfile polyfill para Mac/Linux ───────────────────────────────────
def _patch_os_startfile() -> None:
    if _platform.system() != "Windows":
        import os as _os_para_remendar
        if not hasattr(_os_para_remendar, "startfile"):
            from jarvis.core.plataforma import abrir_no_sistema as _abrir_no_sistema
            _os_para_remendar.startfile = lambda caminho, *a, **k: _abrir_no_sistema(caminho)


# ── Desabilita rodízio de tarefas agressivo ────────────────────────────────
def _configure_task_switching() -> None:
    import sys as _sys
    # NÃO reduzir o rodízio - causava lentidão extrema em produção
    # _sys.setswitchinterval(0.0005)  # NÃO FAZER ISSO


def initialize_bootstrap() -> None:
    """Inicializa todo o bootstrap do JARVIS."""
    _linux_pelo_lancador()
    _saida_de_texto_a_prova_de_emoji()
    _patch_subprocess_popen()
    _patch_os_startfile()
    _configure_task_switching()
    import importlib
    importlib.invalidate_caches()


def ensure_config_files() -> None:
    """Garante que os arquivos de configuração existam."""
    from jarvis.core.paths import get_jarvis_paths
    from jarvis.core.config import ensure_config_migrated
    
    paths = get_jarvis_paths()
    ensure_config_migrated(paths.config_file)


def create_application(config_path: str = None):
    """Cria a aplicação JARVIS."""
    from jarvis.orchestrator import JarvisOrchestrator
    
    # Ensure config files exist
    ensure_config_files()
    
    # Create orchestrator
    app = JarvisOrchestrator()
    
    return app


# Re-export for backward compatibility
__all__ = [
    "initialize_bootstrap",
    "ensure_config_files",
    "create_application",
    "get_base_dir",
]